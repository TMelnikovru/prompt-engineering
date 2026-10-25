import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select, text

from app.config import Settings
from app.db.session import Database
from app.db.url import postgresql_url
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_url() -> str:
    value = os.getenv("HSE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Для интеграционных тестов задайте HSE_TEST_DATABASE_URL")
    database = postgresql_url(value).database
    if not database or not database.endswith("_test"):
        pytest.fail("Используйте отдельную тестовую БД с именем, оканчивающимся на _test")
    return value


def test_migrations_upgrade_downgrade_and_upgrade_again(database_url):
    env = {**os.environ, "HSE_DATABASE_URL": database_url}
    for command in (
        ("upgrade", "head"),
        ("upgrade", "head"),
        ("downgrade", "base"),
        ("upgrade", "head"),
        ("check",),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    async def verify():
        database = Database(Settings(_env_file=None, database_url=database_url))
        try:
            async with database.session() as session:
                version = await session.scalar(text("SELECT version_num FROM alembic_version"))
                assert version == "0002_ingestion_store"
        finally:
            await database.close()

    asyncio.run(verify())


def test_sessions_commit_rollback_and_persistence_across_reconnect(database_url):
    async def verify():
        settings = Settings(_env_file=None, database_url=database_url)
        database = Database(settings)
        metadata = MetaData()
        probe = Table(
            "db_probe_" + uuid4().hex,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("label", String(32), nullable=False),
        )
        try:
            async with database.engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
            async with database.session() as session:
                async with session.begin():
                    await session.execute(insert(probe).values(id=1, label="committed"))
            async with database.session() as session:
                await session.execute(insert(probe).values(id=2, label="not-committed"))
                # Выход без commit должен откатить запись.
            with pytest.raises(RuntimeError, match="test rollback"):
                async with database.session() as session, session.begin():
                    await session.execute(insert(probe).values(id=3, label="failed"))
                    raise RuntimeError("test rollback")
            await database.close()
            database = Database(settings)
            async with database.session() as session:
                labels = (await session.scalars(select(probe.c.label))).all()
                assert labels == ["committed"]
        finally:
            async with database.engine.begin() as connection:
                await connection.run_sync(metadata.drop_all)
            await database.close()

    asyncio.run(verify())


def test_readiness_against_real_postgresql(database_url):
    application = create_app(Settings(_env_file=None, database_url=database_url))
    with TestClient(application) as api:
        response = api.get("/api/v1/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": "connected"}
