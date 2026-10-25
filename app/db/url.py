from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def postgresql_url(value: str) -> URL:
    """Проверить URL, не включив пароль в сообщение об ошибке."""
    try:
        url = make_url(value)
        if url.drivername not in {"postgresql", "postgresql+psycopg"} or not url.database:
            raise ValueError
        return url.set(drivername="postgresql+psycopg")
    except (ArgumentError, ValueError):
        raise ValueError(
            "HSE_DATABASE_URL должен быть PostgreSQL URL с именем базы данных"
        ) from None
