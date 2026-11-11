from typing import Any

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.ingestion import SyncResult, error
from app.api.schemas import ApiError
from app.connectors.dsba import DsbaCatalog, DsbaConnector, DsbaError, DsbaSelection
from app.services.dsba import sync_dsba
from app.services.ingestion import SyncBusy

router = APIRouter(prefix="/api/v1/integrations/dsba", tags=["DSBA Online"])


class DsbaSyncResult(SyncResult):
    source_id: str
    cached: bool = False


@router.get(
    "/catalog",
    response_model=DsbaCatalog,
    description="Текущий публичный каталог программ, курсов, групп и предметов dsba.online.",
    responses={502: {"model": ApiError}},
)
async def catalog(request: Request) -> Any:
    try:
        async with DsbaConnector(request.app.state.settings) as connector:
            return await connector.catalog()
    except DsbaError:
        return error(502, "dsba_catalog_unavailable", "Каталог dsba.online временно недоступен.")


@router.post(
    "/sync",
    response_model=DsbaSyncResult,
    description=(
        "Подключает выбранные во фронтенде программу, курс, группу и подгруппу. "
        "Читает только публичные предметы, материалы и дедлайны. Возвращает source_id, "
        "который нужно передавать в dashboard как dsba_source_id и documents как source_id. "
        "Повторный запрос в течение 60 секунд использует сохранённый результат. "
        "Выбранные наборы обновляются серверным dsba-worker без открытой вкладки. "
        "Это выбор публичной учебной группы, а не вход в личный аккаунт DSBA. "
        "Приватный Classroom и административные действия не вызываются. OpenAI не вызывается."
    ),
    responses={
        400: {"model": ApiError},
        409: {"model": ApiError},
        429: {"model": ApiError},
        502: {"model": DsbaSyncResult | ApiError},
        503: {"model": ApiError},
    },
)
async def synchronize(request: Request, selection: DsbaSelection) -> Any:
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        result = await sync_dsba(database, request.app.state.settings, selection)
        # A failed refresh still returns its persisted run and source identifier.
        return JSONResponse(
            status_code=502 if result["status"] == "failed" else 200,
            content=jsonable_encoder(result),
        )
    except DsbaError as exc:
        if exc.code == "dsba_invalid_selection":
            return error(400, exc.code, "Выбери программу, курс и группу из каталога сайта.")
        if exc.code == "dsba_profile_limit":
            return error(429, exc.code, "Достигнут лимит подключённых учебных групп.")
        return error(502, exc.code, "Не получилось получить данные dsba.online. Попробуй позже.")
    except SyncBusy:
        return error(409, "dsba_sync_in_progress", "DSBA уже обновляется. Попробуй через минуту.")
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище источников недоступно.")
