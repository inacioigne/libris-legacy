import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from libris.core.config import Settings
from libris.main import create_app


def test_lifespan_and_health_without_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_connection(*args: object, **kwargs: object) -> None:
        pytest.fail("Startup/health must not connect to PostgreSQL")

    monkeypatch.setattr(AsyncEngine, "connect", unexpected_connection)
    settings = Settings(_env_file=None, app_env="test")
    app = create_app(settings)
    with TestClient(app) as client:
        assert app.state.database.engine.url.drivername == "postgresql+asyncpg"
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "libris-api"}
        assert response.headers["content-type"] == "application/json"


def test_openapi_exposes_health_contract() -> None:
    with TestClient(create_app(Settings(_env_file=None, app_env="test"))) as client:
        schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Libris API"
    assert schema["paths"]["/health"]["get"]["responses"]["200"]
    assert set(schema["paths"]) == {"/health", "/api/v1/identity/me"} | {
        f"/api/v1/catalog/{collection}{suffix}"
        for collection in ("works", "instances", "items", "agents", "subjects")
        for suffix in ("", "/{id}")
    } | {
        f"/api/v1/catalog/public/{collection}"
        for collection in ("works", "instances", "items", "agents", "subjects")
    } | {
        "/api/v1/catalog/profiles",
        "/api/v1/catalog/profiles/release",
        "/api/v1/catalog/{collection}/validation",
        "/api/v1/catalog/{collection}/{id}/relationships",
        "/api/v1/catalog/{collection}/{id}/workspace/validate",
        "/api/v1/catalog/{collection}/{id}/workspace/publish",
        "/api/v1/vocabularies",
        "/api/v1/vocabularies/{vocabulary_id}/concepts",
        "/api/v1/vocabularies/{vocabulary_id}/concepts/{concept_id}",
        "/api/v1/vocabularies/{vocabulary_id}/validate",
    } | {
        "/api/v1/catalog/{collection}/{id}/" + suffix
        for suffix in (
            "editorial", "editorial/submit", "editorial/approve", "editorial/publish",
            "editorial/return", "editorial/withdraw", "editorial/republish", "editorial/archive",
            "public", "revisions", "revisions/compare", "revisions/{number}",
            "revisions/{number}/snapshot", "revisions/restore", "audit", "audit/{event_id}",
            "history",
        )
    }


def test_cors_allows_only_configured_origin() -> None:
    app = create_app(Settings(_env_file=None, cors_origins=["http://localhost:3000"]))
    with TestClient(app) as client:
        allowed = client.get("/health", headers={"Origin": "http://localhost:3000"})
        denied = client.get("/health", headers={"Origin": "https://example.invalid"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in denied.headers


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:4000"]')
    settings = Settings(_env_file=None)
    assert settings.app_env == "test"
    assert settings.cors_origins == ["http://localhost:4000"]


def test_settings_reject_invalid_configuration() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="invalid")
