import asyncio
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.schemas import ApiError, ApiErrorDetail, Dashboard, PreviewScenario, Readiness, Source
from app.services.dashboard import get_dashboard
from app.services.dsba import dsba_materials, dsba_tasks
from app.services.fake_data import get_fake_dashboard
from app.services.library import list_sources

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get(
    "/dashboard",
    response_model=Dashboard,
    summary="Задания, план дня и состояния источников",
    description=(
        "Задания и состояния источников. dsba_source_id выбирает только подключённую "
        "учебную группу DSBA; без него её задания не показываются. "
        "По умолчанию разработки возвращает явно помеченные учебные примеры. "
        "scenario=empty и scenario=error доступны только в демо-режиме."
        " data_source=fake явно выбирает изолированные фейк-данные даже на сервере. "
        "available_minutes и completed (идентификаторы через запятую) "
        "пересчитывают только фейк-план."
    ),
    responses={
        400: {"model": ApiError, "description": "Сценарий примера отключён"},
        503: {"model": ApiError, "description": "Сценарий ошибки для разработки интерфейса"},
    },
)
async def dashboard(
    request: Request,
    scenario: PreviewScenario = PreviewScenario.DEFAULT,
    dsba_source_id: str | None = Query(default=None, pattern=r"^dsba:[0-9a-f]{32}$"),
    data_source: Literal["live", "fake"] = "live",
    available_minutes: int = Query(default=120, ge=0, le=720),
    completed: str = Query(default="", max_length=4000),
) -> Dashboard | JSONResponse:
    settings = request.app.state.settings
    if not settings.demo_mode and scenario != PreviewScenario.DEFAULT:
        return JSONResponse(
            status_code=400,
            content=ApiError(
                error=ApiErrorDetail(
                    code="preview_disabled",
                    message="Сценарии примеров доступны только в демо-режиме.",
                )
            ).model_dump(),
        )
    if scenario == PreviewScenario.ERROR:
        return JSONResponse(
            status_code=503,
            content=ApiError(
                error=ApiErrorDetail(
                    code="preview_unavailable",
                    message="Пример ошибки загрузки. Попробуй обновить страницу.",
                    retryable=True,
                )
            ).model_dump(),
        )
    if data_source == "fake" and scenario == PreviewScenario.DEFAULT:
        return get_fake_dashboard(settings, available_minutes, set(completed.split(",")))
    result = get_dashboard(settings, scenario)
    database = request.app.state.database
    if database is not None and not settings.demo_mode:
        try:
            stored_sources = await list_sources(database)
            visible = [
                s for s in stored_sources if s["kind"] != "dsba" or s["id"] == dsba_source_id
            ]
            for stored in visible:
                for index, source in enumerate(result.sources):
                    if source.id == stored["id"] or (
                        source.id == "dsba" and stored["id"] == dsba_source_id
                    ):
                        result.sources[index] = Source.model_validate(stored)
            if dsba_source_id:
                result.tasks = await dsba_tasks(database, dsba_source_id)
                result.materials = await dsba_materials(database, dsba_source_id)
                result.plan.note = "Задания получены из DSBA. План дня ещё не рассчитан."
            total = sum(s["document_count"] for s in visible)
            if total or result.tasks or any(s["status"] == "connected" for s in visible):
                result.meta.mode = "live"
                result.meta.notice = (
                    f"В библиотеке {total} записей из подключённых источников. "
                    "Задания DSBA относятся к выбранной группе. Приоритеты — рекомендации. "
                    "Страницы вики ещё не привязаны к твоему курсу."
                )
        except SQLAlchemyError:
            result.meta.notice = "Хранилище источников временно недоступно."
    return result


@router.get("/health", summary="Проверка HTTP-приложения")
def health(request: Request) -> dict[str, str]:
    return {
        "status": "ok",
        "version": __version__,
        "mode": "demo" if request.app.state.settings.demo_mode else "unconfigured",
    }


@router.get(
    "/ready",
    response_model=Readiness,
    summary="Готовность PostgreSQL",
    description=(
        "Выполняет SELECT 1 с ограничением времени. Проверяет доступность БД, "
        "но не версию миграций и не подключение университетских источников. "
        "Отсутствие настройки и недоступность БД возвращают 503."
    ),
    responses={503: {"model": ApiError, "description": "База не настроена или недоступна"}},
)
async def ready(request: Request) -> Readiness | JSONResponse:
    database = request.app.state.database
    if database is None:
        return JSONResponse(
            status_code=503,
            content=ApiError(
                error=ApiErrorDetail(
                    code="database_not_configured",
                    message="База данных не настроена.",
                )
            ).model_dump(),
        )
    try:
        await asyncio.wait_for(
            database.ping(),
            timeout=request.app.state.settings.db_connect_timeout + 1,
        )
    except (SQLAlchemyError, TimeoutError):
        return JSONResponse(
            status_code=503,
            content=ApiError(
                error=ApiErrorDetail(
                    code="database_unavailable",
                    message="База данных временно недоступна.",
                    retryable=True,
                )
            ).model_dump(),
        )
    return Readiness()
