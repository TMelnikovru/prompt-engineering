from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.dsba import router as dsba_router
from app.api.ingestion import router as ingestion_router
from app.api.routes import router
from app.config import Settings
from app.db.session import Database
from app.services.fake_data import FAKE_MATERIALS

ROOT = Path(__file__).resolve().parent.parent


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database = Database(resolved_settings) if resolved_settings.database_url else None
        application.state.database = database
        try:
            yield
        finally:
            if database is not None:
                await database.close()
            application.state.database = None

    application = FastAPI(
        title="HSE Study Agent",
        version=__version__,
        lifespan=lifespan,
        description=(
            "Контракт для разработки сайта учебного агента. "
            "Вики ФКН, DSBA Online по выбранной группе, библиотека PostgreSQL "
            "и сохранённые результаты ИИ-обработки. "
            "Персональные задания, план и напоминания ещё не формируются из вики."
        ),
    )
    application.state.settings = resolved_settings
    application.state.database = None
    application.include_router(router)
    application.include_router(dsba_router)
    application.include_router(ingestion_router)
    application.mount("/static", StaticFiles(directory=ROOT / "frontend" / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "frontend" / "templates")

    @application.get("/demo/materials/{slug}", response_class=HTMLResponse, include_in_schema=False)
    def fake_material(request: Request, slug: str) -> HTMLResponse:
        if slug not in FAKE_MATERIALS:
            raise HTTPException(status_code=404)
        title, course, text = FAKE_MATERIALS[slug]
        return templates.TemplateResponse(
            request=request,
            name="fake-material.html",
            context={"title": title, "course": course, "text": text},
        )

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html")

    return application


app = create_app()
