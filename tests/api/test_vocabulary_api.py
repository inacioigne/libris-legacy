"""Vocabulary HTTP authentication and structural request validation."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from libris.core.config import Settings
from libris.main import create_app
from libris.modules.bibliographic.api.dependencies import get_vocabulary_query_service
from libris.modules.identity.api.dependencies import get_current_actor
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.domain.permissions import Permission


def actor(*permissions: Permission) -> AuthenticatedActor:
    issuer, subject = "https://issuer.example.invalid", "vocabulary-api-test"
    return AuthenticatedActor(
        actor_identifier(issuer, subject),
        issuer,
        subject,
        frozenset(),
        frozenset(permissions),
    )


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/api/v1/vocabularies", None),
        ("get", f"/api/v1/vocabularies/{uuid4()}/concepts", None),
        ("post", f"/api/v1/vocabularies/{uuid4()}/validate", {"uri": "urn:test:concept"}),
    ],
)
def test_vocabulary_routes_require_bearer_authentication(
    method: str, path: str, json: dict[str, str] | None
) -> None:
    app = create_app(Settings(_env_file=None, app_env="test", oidc_enabled=False))
    with TestClient(app) as client:
        response = client.get(path) if method == "get" else client.post(path, json=json)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_vocabulary_routes_require_effective_bibliographic_read_permission() -> None:
    app = create_app(Settings(_env_file=None, app_env="test", oidc_enabled=False))
    app.dependency_overrides[get_current_actor] = lambda: actor(Permission.BIBLIOGRAPHIC_READ)
    with TestClient(app) as client:
        response = client.get("/api/v1/vocabularies")
    assert response.status_code == 403
    assert response.json()["code"] == "authorization.denied"


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "q=%20%20",
        "locale=pt_BR",
        "uri=relative",
        "status=unknown",
    ],
)
def test_concept_search_rejects_invalid_parameters(query: str) -> None:
    app = create_app(Settings(_env_file=None, app_env="test", oidc_enabled=False))
    app.dependency_overrides[get_current_actor] = lambda: actor(
        Permission.BIBLIOGRAPHIC_READ,
        Permission.METADATA_REVISION_READ,
    )
    app.dependency_overrides[get_vocabulary_query_service] = lambda: None
    path = f"/api/v1/vocabularies/{uuid4()}/concepts?{query}"
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "schema.invalid"
