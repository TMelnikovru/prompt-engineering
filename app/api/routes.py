import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.schemas import ApiError, ApiErrorDetail, Dashboard, PreviewScenario, Readiness
from app.services.dashboard import get_dashboard
from app.services.library import list_sources

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get(
    "/dashboard",
    response_model=Dashboard,
    summary="Задания, план дня и состояния источников",
    description=(
        "Каркас API для фронтенда. По умолчанию возвращает явно помеченные учебные примеры. "
        "При HSE_DEMO_MODE=false возвращает пустые данные и mode=unconfigured. "
        "scenario=empty и scenario=error доступны только в демо-режиме."
    ),
    responses={
        400: {"model": ApiError, "description": "Сценарий примера отключён"},
        503: {"model": ApiError, "description": "Сценарий ошибки для разработки интерфейса"},
    },
)
async def dashboard(
    request: Request,
    scenario: PreviewScenario = PreviewScenario.DEFAULT,
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
                    message="Пример ошибки загрузки. Попробуйте обновить страницу.",
                    retryable=True,
                )
            ).model_dump(),
        )
    result = get_dashboard(settings, scenario)
    database = request.app.state.database
    if database is not None and not settings.demo_mode:
        try:
            stored_sources = await list_sources(database)
            for stored in stored_sources:
                for source in result.sources:
                    if source.id == stored["id"]:
                        source.status = stored["status"]
                        source.last_synced_at = stored["last_synced_at"]
                        source.error_message = stored["last_error"]
                if stored["document_count"]:
                    result.meta.mode = "live"
                    result.meta.notice = (
                        f"В библиотеке {stored['document_count']} страниц вики ФКН. "
                        "Принадлежность заданий вашему курсу ещё не определена."
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
