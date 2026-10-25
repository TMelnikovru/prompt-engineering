import hmac
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import ApiError
from app.db.models import AiArtifact
from app.services.ingestion import SyncBusy, sync_wiki
from app.services.library import get_document, list_documents, list_sources

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


class SourceView(BaseModel):
    id: str
    name: str
    kind: str
    base_url: str
    status: str
    document_count: int
    last_synced_at: datetime | None
    last_error: str | None
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


ERRORS = {503: {"model": ApiError, "description": "PostgreSQL не настроен или недоступен"}}


@router.get("/sources", response_model=list[SourceView], responses=ERRORS)
async def sources(request: Request):
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        return await list_sources(database)
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище источников недоступно.")


@router.get("/documents", response_model=DocumentList, responses=ERRORS)
async def documents(
    request: Request,
    source_id: str = "wiki",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
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
)
async def document(request: Request, document_id: UUID):
    database = request.app.state.database
    if database is None:
        return error(503, "database_not_configured", "База данных не настроена.")
    try:
        result = await get_document(database, document_id)
        if result is None:
            return error(404, "document_not_found", "Документ не найден.")
        return result
    except SQLAlchemyError:
        return error(503, "database_unavailable", "Хранилище документов недоступно.")


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
async def latest_summary(request: Request):
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
