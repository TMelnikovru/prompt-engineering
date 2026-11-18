"""Публичные Telegram-каналы через веб-страницу t.me/s/<канал>, без аккаунта и бота.

Это та же страница, что открывается в браузере для любого открытого канала. Читаются
только каналы из явного списка; закрытые каналы так недоступны.
"""

import hashlib
import re
from datetime import UTC, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.connectors.base import DocumentRecord

CHANNEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


class PublicChannelError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def channel_name(ref: str) -> str:
    """@dsba_hse, dsba_hse, t.me/dsba_hse, https://t.me/s/dsba_hse → dsba_hse."""
    value = ref.strip()
    value = re.sub(r"^(https?://)?(www\.)?t(elegram)?\.me/(s/)?", "", value).lstrip("@")
    value = value.split("/")[0].split("?")[0]
    if not CHANNEL.fullmatch(value):
        raise PublicChannelError("telegram_channel_invalid_name")
    return value


def parse_channel_page(
    html: str, name: str, fetched_at: datetime
) -> tuple[str, list[DocumentRecord], int | None]:
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one(".tgme_channel_info_header_title")
    title = header.get_text(" ", strip=True) if header else "@" + name
    records = []
    for node in soup.select(".tgme_widget_message[data-post]"):
        post = node["data-post"]
        message_id = int(post.rsplit("/", 1)[1]) if post.rsplit("/", 1)[-1].isdigit() else None
        moment = node.select_one(".tgme_widget_message_date time[datetime]")
        if message_id is None or moment is None:
            continue
        published = datetime.fromisoformat(moment["datetime"]).astimezone(UTC)
        body = node.select_one(".tgme_widget_message_text")
        for br in body.select("br") if body else []:
            br.replace_with("\n")
        text = body.get_text("", strip=False).strip() if body else ""
        documents = [
            item.get_text(" ", strip=True)
            for item in node.select(".tgme_widget_message_document_title")
        ]
        if documents:
            text += ("\n\n" if text else "") + "Файлы: " + ", ".join(documents)
        if not text:
            continue
        links = [
            {"url": a["href"], "text": a.get_text(" ", strip=True)}
            for a in (body.select("a[href]") if body else [])
            if a["href"].startswith(("http://", "https://"))
        ][:50]
        meta = node.select_one(".tgme_widget_message_meta")
        edited = bool(meta and "edited" in meta.get_text(" ", strip=True).lower())
        url = f"https://t.me/{name}/{message_id}"
        attributes = {
            "chat_id": "@" + name.lower(),
            "chat_title": title,
            "message_id": message_id,
            "published_at": published.isoformat(),
            "edited_at": None,
            "edited": edited,
            "reply_to_message_id": None,
            "attachment": {"names": documents} if documents else None,
        }
        records.append(
            DocumentRecord(
                source_id="telegram_channels",
                external_id=f"@{name.lower()}:{message_id}",
                title=f"{title} · #{message_id}",
                text=text,
                url=url,
                published_at=published,
                fetched_at=fetched_at,
                content_type="text/plain",
                raw_content=text,
                # Правка поста меняет текст, а значит и версию; старая остаётся в истории.
                revision_key="post",
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                links=links,
                attributes=attributes,
            )
        )
    more = soup.select_one("a.tme_messages_more[data-before]")
    before = int(more["data-before"]) if more and more["data-before"].isdigit() else None
    return title, records, before


async def fetch_channel(
    client: httpx.AsyncClient, ref: str, settings: Settings, now: datetime | None = None
) -> list[DocumentRecord]:
    """Последние посты за telegram_history_days, не больше telegram_public_pages страниц."""
    name = channel_name(ref)
    now = now or datetime.now(UTC)
    since = now - timedelta(days=settings.telegram_history_days)
    records: dict[str, DocumentRecord] = {}
    before: int | None = None
    for _ in range(settings.telegram_public_pages):
        url = f"https://t.me/s/{name}" + (f"?before={before}" if before else "")
        try:
            response = await client.get(url)
        except httpx.TimeoutException:
            raise PublicChannelError("telegram_channel_timeout") from None
        except httpx.HTTPError:
            raise PublicChannelError("telegram_channel_network_error") from None
        if response.status_code != 200:
            raise PublicChannelError(f"telegram_channel_http_{response.status_code}")
        if "tgme_channel_info" not in response.text and "tgme_widget_message" not in response.text:
            # Для закрытого или несуществующего канала t.me отдаёт страницу без ленты.
            raise PublicChannelError("telegram_channel_not_public")
        _, page, before = parse_channel_page(response.text, name, now)
        for record in page:
            if record.published_at >= since:
                records[record.external_id] = record
        if not page or not before or min(r.published_at for r in page) < since:
            break
    return sorted(records.values(), key=lambda r: r.attributes["message_id"])
