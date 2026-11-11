from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.url import postgresql_url


class Database:
    def __init__(self, settings: Settings):
        if settings.database_url is None:
            raise ValueError("HSE_DATABASE_URL не задан")
        self.engine = create_async_engine(
            postgresql_url(settings.database_url.get_secret_value()),
            echo=False,
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=0,
            pool_timeout=settings.db_connect_timeout,
            connect_args={"connect_timeout": settings.db_connect_timeout},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # Session-level advisory locks must not occupy/re-enter the data session pool.
        self.lock_engine = create_async_engine(
            postgresql_url(settings.database_url.get_secret_value()),
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={"connect_timeout": settings.db_connect_timeout},
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        # Закрытие сессии откатывает незавершённую транзакцию. Commit — явно в сервисе.
        async with self.sessions() as session:
            yield session

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self.engine.dispose()
        await self.lock_engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database | None = request.app.state.database
    if database is None:
        raise HTTPException(status_code=503, detail="База данных не настроена")
    async with database.session() as session:
        yield session
