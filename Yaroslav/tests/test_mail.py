import asyncio
import imaplib
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx
import pytest

from app.config import Settings
from app.connectors import mail as mail_module
from app.connectors.mail import MailConnector, MailError, parse_message
from app.connectors.yandex_oauth import (
    DeviceCode,
    start_device_flow,
    wait_for_token,
    write_env_value,
)
from app.services.source_catalog import source_error_message

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def settings(**overrides) -> Settings:
    values = {"mail_username": "student@edu.hse.ru", "mail_password": "app-password"}
    return Settings(_env_file=None, **{**values, **overrides})


def raw_message(
    sender="Преподаватель <teacher@hse.ru>",
    subject="Дедлайн ДЗ 3",
    body="Сдать до 20 ноября 23:59.",
    message_id="<abc@hse.ru>",
    html=None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "student@edu.hse.ru"
    message["Subject"] = subject
    message["Date"] = "Tue, 10 Nov 2026 12:00:00 +0300"
    if message_id:
        message["Message-ID"] = message_id
    if html:
        message.set_content(html, subtype="html")
    else:
        message.set_content(body)
        message.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="hw3.pdf")
    return message.as_bytes()


def test_message_becomes_document_with_evidence_and_stable_id():
    record = parse_message(raw_message(), "INBOX", settings(), NOW)
    assert record.source_id == "mail"
    assert record.title == "Дедлайн ДЗ 3"
    assert "Сдать до 20 ноября 23:59." in record.text
    assert "Вложения: hw3.pdf" in record.text
    assert record.published_at.utcoffset().total_seconds() == 3 * 3600
    assert record.attributes["sender"] == "teacher@hse.ru"
    again = parse_message(raw_message(body="Другой текст"), "Archive", settings(), NOW)
    # Same Message-ID → same document; changed text becomes a new version, not a duplicate.
    assert again.external_id == record.external_id
    assert again.content_hash != record.content_hash


def test_sender_filter_keeps_private_mail_out_of_database_and_model():
    private = raw_message(sender="Друг <friend@gmail.com>")
    assert parse_message(private, "INBOX", settings(), NOW) is None
    sub_domain = raw_message(sender="Учебный офис <office@edu.hse.ru>")
    assert parse_message(sub_domain, "INBOX", settings(), NOW) is not None
    everything = settings(mail_allowed_sender_domains=[])
    assert parse_message(private, "INBOX", everything, NOW) is not None


def test_html_only_message_is_converted_to_text_with_links():
    html = '<p>Материалы: <a href="https://lms.hse.ru/x">лекция</a></p><script>x()</script>'
    record = parse_message(raw_message(html=html, message_id=None), "INBOX", settings(), NOW)
    assert "Материалы: лекция" in record.text
    assert "x()" not in record.text
    assert record.links == [{"url": "https://lms.hse.ru/x", "text": "лекция"}]
    assert record.attributes["has_message_id"] is False


class FakeImap:
    instances: list["FakeImap"] = []

    def __init__(self, *args, **kwargs):
        self.selected = []
        self.fetch_specs = []
        self.logged_out = False
        FakeImap.instances.append(self)

    def login(self, user, password):
        if password != "app-password":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED student@edu.hse.ru")

    def select(self, folder, readonly=False):
        self.selected.append((folder, readonly))
        return ("NO", [b""]) if folder == '"Missing"' else ("OK", [b"3"])

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"1 2 3"]
        self.fetch_specs.append(args[1])
        uid = args[0].decode()
        sender = "spam@example.com" if uid == "2" else "teacher@hse.ru"
        raw = raw_message(sender=sender, message_id=f"<{uid}@hse.ru>")
        return "OK", [(b"1 (BODY[] {1})", raw), b")"]

    def authenticate(self, mechanism, callback):
        self.auth = (mechanism, callback(b""))
        if b"auth=Bearer good-token" not in self.auth[1]:
            raise imaplib.IMAP4.error("AUTHENTICATE failed")
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


def test_connector_reads_without_marking_seen_and_reports_bad_folders(monkeypatch):
    FakeImap.instances.clear()
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", FakeImap)
    connector = MailConnector(settings(mail_folders=["INBOX", "Missing"], mail_max_messages=2))
    batch = asyncio.run(connector.collect())
    client = FakeImap.instances[0]
    assert all(readonly for _, readonly in client.selected)
    assert set(client.fetch_specs) == {"(BODY.PEEK[])"}
    assert client.logged_out
    # Newest UIDs first, limit honoured, foreign sender skipped.
    assert len(batch.messages) == 1 and batch.skipped == 1
    assert batch.has_more
    assert batch.errors == [{"folder": "Missing", "code": "mail_folder_unavailable"}]


def test_auth_failure_and_missing_config_do_not_leak_details(monkeypatch):
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", FakeImap)
    with pytest.raises(MailError) as error:
        asyncio.run(MailConnector(settings(mail_password="wrong")).collect())
    assert error.value.code == "mail_auth_failed"
    assert "student" not in str(error.value)
    with pytest.raises(MailError) as error:
        MailConnector(Settings(_env_file=None))
    assert error.value.code == "mail_not_configured"
    assert "пароль приложения" in source_error_message("mail_auth_failed")
    assert "app-password" not in repr(settings())


def test_oauth_token_uses_xoauth2_and_never_needs_password(monkeypatch):
    FakeImap.instances.clear()
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", FakeImap)
    oauth = Settings(
        _env_file=None, mail_username="student@edu.hse.ru", mail_oauth_token="good-token"
    )
    batch = asyncio.run(MailConnector(oauth).collect())
    mechanism, payload = FakeImap.instances[0].auth
    assert mechanism == "XOAUTH2"
    assert payload == b"user=student@edu.hse.ru\x01auth=Bearer good-token\x01\x01"
    assert batch.messages
    expired = Settings(
        _env_file=None, mail_username="student@edu.hse.ru", mail_oauth_token="old-token"
    )
    with pytest.raises(MailError) as error:
        asyncio.run(MailConnector(expired).collect())
    assert error.value.code == "mail_oauth_invalid"
    assert "app.mail login" in source_error_message("mail_oauth_invalid")
    assert "good-token" not in repr(oauth)


def oauth_client(responses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, dict(httpx.QueryParams(request.content.decode()))))
        status, body = responses.pop(0)
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def test_device_flow_waits_for_student_and_respects_slow_down():
    settings = Settings(_env_file=None, mail_oauth_client_id="cid", mail_oauth_client_secret="sec")
    client, calls = oauth_client(
        [
            (
                200,
                {
                    "device_code": "dev",
                    "user_code": "ABCD",
                    "verification_url": "https://ya.ru/device",
                    "interval": 5,
                    "expires_in": 300,
                },
            ),
            (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down"}),
            (200, {"access_token": "tok", "expires_in": 31536000}),
        ]
    )
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def run():
        async with client:
            code = await start_device_flow(settings, client)
            return code, await wait_for_token(settings, client, code, sleep=fake_sleep)

    code, token = asyncio.run(run())
    assert code.user_code == "ABCD"
    assert token.access_token == "tok" and token.expires_in == 31536000
    assert sleeps == [5, 5, 10]
    assert calls[0] == ("/device/code", {"client_id": "cid", "device_name": "HSE Study Agent"})
    assert calls[1][1] == {
        "grant_type": "device_code",
        "code": "dev",
        "client_id": "cid",
        "client_secret": "sec",
    }


def test_device_flow_reports_denial_and_missing_client():
    settings = Settings(_env_file=None, mail_oauth_client_id="cid")
    client, _ = oauth_client([(400, {"error": "access_denied"})])
    code = DeviceCode("dev", "ABCD", "https://ya.ru/device", 1, 60)

    async def no_sleep(_):
        return None

    with pytest.raises(MailError) as error:
        asyncio.run(wait_for_token(settings, client, code, sleep=no_sleep))
    assert error.value.code == "mail_oauth_access_denied"
    with pytest.raises(MailError) as error:
        asyncio.run(start_device_flow(Settings(_env_file=None), client))
    assert error.value.code == "mail_oauth_client_missing"


def test_token_is_written_to_env_without_touching_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("HSE_MAIL_USERNAME=a@edu.hse.ru\nHSE_MAIL_OAUTH_TOKEN=old\n", encoding="utf-8")
    write_env_value(env, "HSE_MAIL_OAUTH_TOKEN", "new")
    write_env_value(env, "OTHER", "x")
    assert env.read_text(encoding="utf-8").splitlines() == [
        "HSE_MAIL_USERNAME=a@edu.hse.ru",
        "HSE_MAIL_OAUTH_TOKEN=new",
        "OTHER=x",
    ]
