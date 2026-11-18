import hmac
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import ApiError, Source
from app.db.models import AiArtifact
from app.services.analysis import AiError
from app.services.ingestion import SyncBusy, sync_wiki
from app.services.library import get_document, list_documents, list_sources
from app.services.telegram import latest_telegram_digest, summarize_telegram, sync_telegram

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


class SourceView(Source):
    """The dashboard's source contract, plus operator and library metadata."""

    kind: str
    base_url: str
    document_count: int
    last_error: str | None = Field(
        description="Технический код для диагностики. Пользователю показывайте error_message."
    )
    scope: dict[str, Any]
    last_run: dict[str, Any] | None


class DocumentPreview(BaseModel):
    id: UUID
    source_id: str
    external_id: str
    title: str
    url: str
    version_id: UUID
    content_hash: str
    excerpt: str
    last_seen_at: datetime


class DocumentList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DocumentPreview]


class DocumentDetail(BaseModel):
    id: UUID
    source_id: str
    title: str
    url: str
    version_id: UUID
    content_hash: str
    content_text: str
    attributes: dict[str, Any]
    fetched_at: datetime
    last_seen_at: datetime
    version_count: int
    is_active: bool = True
    analysis: dict[str, Any] | None = None


class SyncResult(BaseModel):
    run_id: UUID
    status: str
    created: int
    updated: int
    unchanged: int
    errors: list[dict[str, str]]
    has_more: bool
    scope: dict[str, Any]


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "retryable": status >= 500}},
    )


ERRORS = {
    401: {"model": ApiError, "description": "Нужен серверный X-Ingestion-Token"},
    503: {"model": ApiError, "description": "Хранилище или доступ не настроены"},
}


def operator_error(request: Request, token: str | None) -> JSONResponse | None:
    expected = request.app.state.settings.ingestion_token
    if expected is None:
        return error(503, "ingestion_disabled", "Доступ к библиотеке не настроен.")
    if not token or not hmac.compare_digest(token.encode(), expected.get_secret_value().encode()):
        return error(401, "invalid_ingestion_token", "Нет доступа к учебным данным.")
    return None


# Личная переписка: записи и состояние этих источников видны только оператору.
# Публичные источники (вики ФКН, DSBA) читаются без ключа, как их использует сайт.
PRIVATE_SOURCE_IDS = frozenset({"telegram"})


@router.get(
    "/sources",
    response_model=list[SourceView],
    responses=ERRORS,
    description="Без X-Ingestion-Token возвращает только публичные источники, без Telegram.",
)
async def sources(request: Request, x_ingestion_token: Annotated[str | None, Header()] = None):
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        result = await list_sources(database)
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище источников недоступно.")
    if operator_error(request, x_ingestion_token) is not None:
        result = [source for source in result if source["id"] not in PRIVATE_SOURCE_IDS]
    return result


@router.get(
    "/documents",
    response_model=DocumentList,
    responses=ERRORS,
    description="Записи Telegram требуют X-Ingestion-Token; вики и DSBA доступны без него.",
)
async def documents(
    request: Request,
    source_id: str = "wiki",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    x_ingestion_token: Annotated[str | None, Header()] = None,
):
    if source_id in PRIVATE_SOURCE_IDS:
        denied = operator_error(request, x_ingestion_token)
        if denied is not None:
            return denied
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        return await list_documents(database, source_id, limit, offset)
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище документов недоступно.")


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetail,
    responses={**ERRORS, 404: {"model": ApiError}},
    description="Записи Telegram требуют X-Ingestion-Token; вики и DSBA доступны без него.",
)
async def document(
    request: Request,
    document_id: UUID,
    x_ingestion_token: Annotated[str | None, Header()] = None,
):
    denied = operator_error(request, x_ingestion_token)
    database = request.app.state.database
    if database is None:
        # Без хранилища нельзя убедиться, что запись публичная.
        return denied or error(503, "database_not_configured", "База данных не настроена.")
    try:
        result = await get_document(database, document_id)
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище документов недоступно.")
    if result is not None and result["source_id"] in PRIVATE_SOURCE_IDS and denied is not None:
        return denied
    if result is None:
        return error(404, "document_not_found", "Документ не найден.")
    return result


@router.post(
    "/integrations/wiki/sync",
    response_model=SyncResult,
    description=(
        "Читает ограниченный настройками набор страниц. Нужен X-Ingestion-Token, "
        "совпадающий с HSE_INGESTION_TOKEN на сервере. Без настройки токена "
        "HTTP-запуск отключён; CLI и worker доступны оператору сервера. "
        "Не вызывает OpenAI. Полный обход вики не подразумевается: scope задаёт границы."
    ),
    responses={
        **ERRORS,
        401: {"model": ApiError},
        409: {"model": ApiError},
        502: {"model": SyncResult},
    },
)
async def synchronize(request: Request, x_ingestion_token: Annotated[str | None, Header()] = None):
    settings = request.app.state.settings
    expected = settings.ingestion_token
    if expected is None:
        return error(503, "ingestion_disabled", "HTTP-запуск синхронизации не настроен.")
    if not x_ingestion_token or not hmac.compare_digest(
        x_ingestion_token.encode(), expected.get_secret_value().encode()
    ):
        return error(401, "invalid_ingestion_token", "Нет доступа к синхронизации.")
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        result = await sync_wiki(database, settings)
        return JSONResponse(
            status_code=502 if result["status"] == "failed" else 200,
            content=jsonable_encoder(result),
        )
    except SyncBusy:
        return error(409, "sync_in_progress", "Синхронизация уже выполняется.")
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище источников недоступно.")


@router.get("/knowledge/summary", responses=ERRORS)
async def latest_summary(
    request: Request, x_ingestion_token: Annotated[str | None, Header()] = None
):
    denied = operator_error(request, x_ingestion_token)
    if denied is not None:
        return denied
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        async with database.session() as session:
            artifact = await session.scalar(
                select(AiArtifact)
                .where(AiArtifact.kind == "summary")
                .order_by(AiArtifact.created_at.desc())
                .limit(1)
            )
            if artifact is None:
                return {"summary": None}
            return {
                "id": str(artifact.id),
                "model": artifact.model,
                "created_at": artifact.created_at,
                "summary": artifact.payload,
            }
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище сводок недоступно.")


@router.post(
    "/integrations/telegram/sync",
    response_model=SyncResult,
    description="Читает только HSE_TELEGRAM_CHATS. Требует предварительного входа через CLI. "
    "Сохраняет сообщения и правки в PostgreSQL; OpenAI не вызывает.",
    responses={**ERRORS, 409: {"model": ApiError}, 502: {"model": SyncResult}},
)
async def synchronize_telegram(
    request: Request,
    x_ingestion_token: Annotated[str | None, Header()] = None,
):
    denied = operator_error(request, x_ingestion_token)
    if denied is not None:
        return denied
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        result = await sync_telegram(database, request.app.state.settings)
        return JSONResponse(
            status_code=502 if result["status"] == "failed" else 200,
            content=jsonable_encoder(result),
        )
    except SyncBusy:
        return error(409, "sync_in_progress", "Telegram уже синхронизируется.")
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище Telegram недоступно.")


@router.get("/knowledge/telegram", responses=ERRORS)
async def telegram_digest(
    request: Request,
    x_ingestion_token: Annotated[str | None, Header()] = None,
):
    denied = operator_error(request, x_ingestion_token)
    if denied is not None:
        return denied
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        return await latest_telegram_digest(database, request.app.state.settings)
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище Telegram недоступно.")


@router.post(
    "/knowledge/telegram/refresh",
    description="Передаёт выбранное окно сохранённых сообщений в OpenAI, сохраняет сводку "
    "с цитатами. Не читает Telegram. Неизменные входные данные используют кеш.",
    responses={**ERRORS, 409: {"model": ApiError}, 502: {"model": ApiError}},
)
async def refresh_telegram_digest(
    request: Request,
    x_ingestion_token: Annotated[str | None, Header()] = None,
):
    denied = operator_error(request, x_ingestion_token)
    if denied is not None:
        return denied
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        return await summarize_telegram(database, request.app.state.settings)
    except AiError as exc:
        return error(502, str(exc), "Не удалось обновить ИИ-сводку Telegram.")
    except SyncBusy:
        return error(409, "analysis_in_progress", "ИИ-сводка уже обновляется.")
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище Telegram недоступно.")
