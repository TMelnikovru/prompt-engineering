"""Синхронизация дедлайнов SmartLMS и их показ как заданий."""

from app.config import Settings
from app.connectors.smartlms import SmartLmsConnector, SmartLmsError
from app.db.session import Database
from app.services.personal_sync import Batch, sync_personal_source

SOURCE_ID = "smartlms"


async def sync_smartlms(database: Database, settings: Settings) -> dict:
    async def collect() -> Batch:
        try:
            async with SmartLmsConnector(settings) as connector:
                records, has_more = await connector.deadlines()
        except SmartLmsError as exc:
            return Batch([], [{"code": exc.code}])
        # Календарь действий — полный снимок окна: сданное задание из него исчезает.
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
            "interval_seconds": settings.smartlms_interval_seconds,
        },
        collect=collect,
    )
