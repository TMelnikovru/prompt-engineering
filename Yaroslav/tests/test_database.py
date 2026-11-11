import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.db.url import postgresql_url
from app.main import create_app


def test_postgresql_url_keeps_encoded_password_without_exposing_it():
    secret = "example%password:with@symbols"
    value = "postgresql://user:example%25password%3Awith%40symbols@localhost/app"
    settings = Settings(_env_file=None, database_url=SecretStr(value))
    url = postgresql_url(settings.database_url.get_secret_value())
    assert url.drivername == "postgresql+psycopg"
    assert url.password == secret
    assert value not in repr(settings)
    assert secret not in repr(url)


@pytest.mark.parametrize(
    "value",
    [
        "sqlite:///do-not-log-this",
        "postgresql://user:do-not-log-this@localhost",
        "not-a-url-do-not-log-this",
    ],
)
def test_invalid_database_configuration_is_rejected_without_echoing_secrets(value):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url=value)
    assert "do-not-log-this" not in str(error.value)


def test_database_is_optional_for_frontend_but_readiness_is_honest():
    application = create_app(Settings(_env_file=None, database_url=None))
    with TestClient(application) as api:
        assert api.get("/").status_code == 200
        assert api.get("/api/v1/dashboard").status_code == 200
        assert api.get("/api/v1/health").status_code == 200
        response = api.get("/api/v1/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_not_configured"


def test_unreachable_database_does_not_break_liveness_or_expose_credentials():
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:do-not-log-this@127.0.0.1:1/hse_agent_test",
        db_connect_timeout=1,
    )
    application = create_app(settings)
    with TestClient(application) as api:
        assert api.get("/api/v1/health").status_code == 200
        response = api.get("/api/v1/ready")
        assert response.status_code == 503
        assert response.json()["error"] == {
            "code": "database_unavailable",
            "message": "База данных временно недоступна.",
            "retryable": True,
        }
        assert "do-not-log-this" not in response.text
        assert "postgresql" not in response.text
    assert application.state.database is None
