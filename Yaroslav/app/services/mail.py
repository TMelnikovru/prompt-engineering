"""Синхронизация почты в общую схему источников, документов и версий."""

from app.config import Settings
from app.connectors.mail import MailBatch, MailConnector, MailError
from app.db.session import Database
from app.services.personal_sync import Batch, sync_personal_source

SOURCE_ID = "mail"


async def sync_mail(database: Database, settings: Settings, batch: MailBatch | None = None) -> dict:
    """Одна логика для воркера и ручного запуска.

    Письма за пределами окна `mail_lookback_days` не снимаются с публикации:
    окно сдвигается, а старое письмо от этого не перестаёт существовать.
    """

    async def collect() -> Batch:
        mail = batch
        if mail is None:
            try:
                mail = await MailConnector(settings).collect()
            except MailError as exc:
                mail = MailBatch(errors=[{"code": exc.code}])
        return Batch(mail.messages, mail.errors, mail.has_more, mail.skipped, complete=False)

    return await sync_personal_source(
        database,
        source_id=SOURCE_ID,
        kind="mail",
        name="Почта",
        base_url=settings.mail_web_url,
        scope={
            "host": settings.mail_imap_host,
            "folders": settings.mail_folders,
            "lookback_days": settings.mail_lookback_days,
            "max_messages": settings.mail_max_messages,
            "allowed_sender_domains": settings.mail_allowed_sender_domains,
            "interval_seconds": settings.mail_interval_seconds,
            "transport": "collector" if batch else "direct",
        },
        collect=collect,
    )
