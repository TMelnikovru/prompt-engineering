from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.schemas import ApiError, ApiErrorDetail, Dashboard, PreviewScenario
from app.services.dashboard import get_dashboard

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
def dashboard(
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
    return get_dashboard(settings, scenario)


@router.get("/health", summary="Проверка HTTP-приложения")
def health(request: Request) -> dict[str, str]:
    return {
        "status": "ok",
        "version": __version__,
        "mode": "demo" if request.app.state.settings.demo_mode else "unconfigured",
    }
