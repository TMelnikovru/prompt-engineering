from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.routes import router
from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(
        title="HSE Study Agent",
        version=__version__,
        description=(
            "Контракт для разработки сайта учебного агента. "
            "Живые интеграции, LLM и фоновые напоминания пока не реализованы."
        ),
    )
    application.state.settings = settings or Settings()
    application.include_router(router)
    application.mount("/static", StaticFiles(directory=ROOT / "frontend" / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "frontend" / "templates")

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html")

    return application


app = create_app()
