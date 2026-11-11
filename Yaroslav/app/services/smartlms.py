"""Синхронизация дедлайнов SmartLMS и их показ как заданий."""

from app.config import Settings
from app.connectors.base import DocumentRecord
from app.connectors.smartlms import SmartLmsConnector, SmartLmsError
from app.connectors.smartlms_calendar import fetch_calendar
from app.db.session import Database
from app.services.personal_sync import Batch, sync_personal_source

SOURCE_ID = "smartlms"


def configured(settings: Settings) -> bool:
    return settings.smartlms_token is not None or settings.smartlms_calendar_url is not None


async def collect_deadlines(settings: Settings) -> tuple[list[DocumentRecord], bool, str]:
    """Ключ API точнее (сданное исчезает), ссылка календаря доступна всем студентам."""
    if settings.smartlms_token is not None:
        async with SmartLmsConnector(settings) as connector:
            records, has_more = await connector.deadlines()
        return records, has_more, "api"
    records, has_more = await fetch_calendar(settings)
    return records, has_more, "calendar"


async def sync_smartlms(database: Database, settings: Settings) -> dict:
    async def collect() -> Batch:
        try:
            records, has_more, _ = await collect_deadlines(settings)
        except SmartLmsError as exc:
            return Batch([], [{"code": exc.code}])
        # Оба способа дают полный снимок окна: ушедшие из него события снимаются.
        return Batch(records, [], has_more=has_more, complete=True)

    return await sync_personal_source(
        database,
        source_id=SOURCE_ID,
        kind="smartlms",
        name="SmartLMS",
        base_url=settings.smartlms_base_url,
        scope={
            "lookback_days": settings.smartlms_lookback_days,
            "lookahead_days": settings.smartlms_lookahead_days,
            "max_events": settings.smartlms_max_events,
            "mode": "api" if settings.smartlms_token is not None else "calendar",
            "interval_seconds": settings.smartlms_interval_seconds,
        },
        collect=collect,
    )
