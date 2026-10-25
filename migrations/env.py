from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import Settings
from app.db.base import Base
from app.db.url import postgresql_url

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

# Будущие ORM-модели нужно импортировать здесь, чтобы autogenerate видел таблицы.
target_metadata = Base.metadata
settings = Settings()
if settings.database_url is None:
    raise RuntimeError("Для миграций задайте HSE_DATABASE_URL в окружении или .env")
url = postgresql_url(settings.database_url.get_secret_value())


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Миграции — отдельная синхронная CLI-команда. Сайт использует AsyncEngine.
    engine = create_engine(
        url,
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": settings.db_connect_timeout},
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
