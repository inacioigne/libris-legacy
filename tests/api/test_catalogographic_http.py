"""Catalog composition, dependencies and operational M05.03 HTTP contract."""

import asyncio
import json
from collections.abc import Callable
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from libris import main
from libris.api.problems import classify, handle_request_validation
from libris.core.config import Settings
from libris.core.correlation import request_id_context
from libris.infrastructure.database import Database
from libris.modules.bibliographic.api.dependencies import get_session_factory
from libris.modules.bibliographic.api.router import router
from libris.modules.bibliographic.api.schemas import (
    AgentCreate,
    InstanceCreate,
    ItemCreate,
    SubjectCreate,
    WorkCreate,
)
from libris.modules.identity.api.dependencies import (
    get_authorization_guard,
    get_current_actor,
    get_execution_context,
)
from libris.modules.identity.domain.authorization import Operation as O
from libris.modules.identity.domain.authorization import ResourceContext, ResourceKind
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.domain.permissions import Permission as P


def actor(*permissions: P) -> AuthenticatedActor:
    issuer, subject = "https://issuer.example.invalid", "synthetic-test-actor"
    return AuthenticatedActor(
        actor_identifier(issuer, subject),
        issuer,
        subject,
        frozenset(),
        frozenset(permissions),
    )


def actor_override(value: AuthenticatedActor) -> Callable[[], AuthenticatedActor]:
    return lambda: value


def test_catalog_composition_point_is_included(monkeypatch: pytest.MonkeyPatch) -> None:
    # Replace only the composition point to prove create_app includes it. This
    # executable probe belongs exclusively to the test, never the product router.
    probe = APIRouter(prefix=router.prefix, tags=router.tags)

    @probe.get("/composition-probe")
    def composed() -> dict[str, bool]:
        return {"composed": True}

    monkeypatch.setattr(main, "catalog_router", probe)
    app = main.create_app(Settings(_env_file=None, app_env="test"))
    with TestClient(app) as client:
        assert client.get("/api/v1/catalog/composition-probe").json() == {"composed": True}
    assert router.prefix == "/api/v1/catalog"
    assert router.tags == ["catalog"]
    assert len(router.routes) == 18  # CRUD and capabilities/editorial/listing composition points


def test_lifespan_factory_and_legacy_routes_without_connections() -> None:
    app = main.create_app(Settings(_env_file=None, app_env="test", oidc_enabled=False))
    # Factory access and liveness must not open a database connection.
    with (
        patch("sqlalchemy.ext.asyncio.AsyncEngine.connect", side_effect=AssertionError),
        patch(
            "libris.infrastructure.database.Database.close",
            autospec=True,
            side_effect=Database.close,
        ) as close,
    ):
        with TestClient(app) as client:
            request = Request({"type": "http", "app": app})
            factory = get_session_factory(request)
            assert factory is app.state.database.sessions
            first, second = factory(), factory()
            assert first is not second
            assert not first.in_transaction() and not second.in_transaction()
            asyncio.run(first.close())
            asyncio.run(second.close())
            assert client.get("/health").json() == {
                "status": "ok",
                "service": "libris-api",
            }
            assert client.get("/api/v1/identity/me").status_code == 401
        close.assert_awaited_once_with(app.state.database)


def test_openapi_only_describes_real_operations_and_schemas() -> None:
    with TestClient(main.create_app(Settings(_env_file=None, app_env="test"))) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert set(schema["paths"]) == {"/health", "/api/v1/identity/me"} | {
            f"/api/v1/catalog/{collection}{suffix}"
            for collection in ("works", "instances", "items", "agents", "subjects")
            for suffix in ("", "/{id}")
        } | {
            "/api/v1/catalog/profiles",
            "/api/v1/catalog/profiles/release",
            "/api/v1/catalog/{collection}/validation",
            "/api/v1/catalog/{collection}/{id}/relationships",
        } | {
            "/api/v1/catalog/{collection}/{id}/" + suffix
            for suffix in (
                "editorial", "editorial/submit", "editorial/approve", "editorial/publish",
                "editorial/return", "editorial/withdraw",
                "editorial/republish", "editorial/archive",
                "public", "revisions", "revisions/compare", "revisions/{number}",
                "revisions/{number}/snapshot", "revisions/restore", "audit", "audit/{event_id}",
                "history",
            )
        } | {
            f"/api/v1/catalog/public/{collection}"
            for collection in ("works", "instances", "items", "agents", "subjects")
        } | {
            "/api/v1/vocabularies",
            "/api/v1/vocabularies/{vocabulary_id}/concepts",
            "/api/v1/vocabularies/{vocabulary_id}/concepts/{concept_id}",
            "/api/v1/vocabularies/{vocabulary_id}/validate",
        }
        assert schema["info"]["version"] == "0.1.0"
        operations = [op for path in schema["paths"].values() for op in path.values()]
        assert len({op["operationId"] for op in operations}) == 53
        for path, model in (
            ("/health", "HealthResponse"),
            ("/api/v1/identity/me", "IdentityResponse"),
        ):
            operation = schema["paths"][path]["get"]
            assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{model}"
            }
            assert model in schema["components"]["schemas"]
        assert schema["paths"]["/api/v1/identity/me"]["get"]["security"] == [{"HTTPBearer": []}]
        for path in schema["paths"]:
            if path.startswith("/api/v1/vocabularies"):
                for operation in schema["paths"][path].values():
                    assert operation["security"] == [{"HTTPBearer": []}]
        assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
        for collection, name in (
            ("works", "Work"),
            ("instances", "Instance"),
            ("items", "Item"),
            ("agents", "Agent"),
            ("subjects", "Subject"),
        ):
            path = f"/api/v1/catalog/{collection}"
            assert set(schema["paths"][path]) == {"get", "post"}
            assert set(schema["paths"][path + "/{id}"]) == {"get", "patch"}
            assert schema["paths"][path]["post"]["security"] == [{"HTTPBearer": []}]
            for suffix in ("Create", "Read", "Update"):
                assert name + suffix in schema["components"]["schemas"]


def test_openapi_declared_errors_match_problem_details_transport() -> None:
    schema = main.create_app(Settings(_env_file=None, app_env="test")).openapi()
    for path, methods in schema["paths"].items():
        if not path.startswith(("/api/v1/catalog", "/api/v1/vocabularies")):
            continue
        for operation in methods.values():
            for status, response in operation["responses"].items():
                if status.isdigit() and int(status) >= 400:
                    assert response["content"] == {
                        "application/problem+json": {
                            "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                        }
                    }, (path, status)


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token", "Basic forged"])
def test_identity_401_cannot_be_bypassed_by_client_context(
    authorization: str | None,
) -> None:
    app = main.create_app(Settings(_env_file=None, app_env="test", oidc_enabled=True))
    headers = {
        "actor_id": "forged",
        "roles": "libris-admin",
        "permissions": "system:admin",
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    with TestClient(app) as client:
        response = client.get("/api/v1/identity/me?technical_context=admin", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == "authentication.required"
    assert "forged" not in response.text and "invalid-token" not in response.text


def test_identity_response_preserves_dependency_override_boundary() -> None:
    app = main.create_app(Settings(_env_file=None, app_env="test"))
    trusted = actor(P.BIBLIOGRAPHIC_READ)
    app.dependency_overrides[get_current_actor] = lambda: trusted
    with TestClient(app) as client:
        response = client.get("/api/v1/identity/me")
    assert response.status_code == 200
    assert response.json() == {
        "actor_id": trusted.actor_id,
        "roles": [],
        "permissions": ["bibliographic:read"],
    }


@pytest.mark.parametrize("token", [None, "invalid-token"])
def test_authentication_dependency_in_isolation(token: str | None) -> None:
    app = main.create_app(Settings(_env_file=None, app_env="test", oidc_enabled=True))
    with TestClient(app):
        credentials = (
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token) if token else None
        )
        with pytest.raises(HTTPException) as caught:
            asyncio.run(
                get_current_actor(
                    credentials,
                    app.state.identity_service,
                    Request({"type": "http", "headers": []}),
                )
            )
    assert caught.value.status_code == 401
    assert caught.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize("resource_name", ["works", "instances", "items", "agents", "subjects"])
def test_context_and_operation_policy_in_isolation(resource_name: str) -> None:
    authority = resource_name in {"agents", "subjects"}
    kind = ResourceKind.AUTHORITY if authority else ResourceKind.BIBLIOGRAPHIC
    operation = O.AUTHORITY_READ_EDITORIAL if authority else O.BIBLIOGRAPHIC_READ_EDITORIAL
    read = P.AUTHORITY_READ if authority else P.BIBLIOGRAPHIC_READ
    guard = get_authorization_guard()
    for grants in ((), (read,), (P.METADATA_REVISION_READ,), (P.SYSTEM_ADMIN,)):
        context = asyncio.run(get_execution_context(actor(*grants)))
        with pytest.raises(PermissionDenied) as caught:
            guard.require(context=context, operation=operation, resource=ResourceContext(kind))
        assert classify(caught.value) == (403, "authorization.denied")
    context = asyncio.run(get_execution_context(actor(read, P.METADATA_REVISION_READ)))
    guard.require(context=context, operation=operation, resource=ResourceContext(kind))
    public = O.AUTHORITY_READ_PUBLIC if authority else O.BIBLIOGRAPHIC_READ_PUBLIC
    guard.require(
        context=None,
        operation=public,
        resource=ResourceContext(kind, "published", 1, 1),
    )
    with pytest.raises(PermissionDenied):
        guard.require(
            context=None,
            operation=public,
            resource=ResourceContext(kind, "published", 1, 2),
        )


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("GET", "/api/v1/catalog/works", 401),
        ("POST", "/api/v1/catalog/works", 401),
        ("GET", "/api/v1/catalog/works/00000000-0000-4000-8000-000000000001", 401),
        ("POST", "/health", 405),
    ],
)
def test_http_errors_and_correlation(method: str, path: str, status: int) -> None:
    identifier = str(uuid4())
    with TestClient(main.create_app(Settings(_env_file=None, app_env="test"))) as client:
        response = client.request(method, path, headers={"X-Request-ID": identifier})
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["x-request-id"] == identifier
    problem = response.json()
    assert problem["request_id"] == identifier
    assert problem["instance"] == f"urn:uuid:{identifier}"
    assert problem["status"] == status
    assert problem["code"] == {401: "authentication.required", 405: "http.405"}[status]
    if status == 405:
        assert response.headers["allow"] == ("POST" if path.endswith("/works") else "GET")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert request_id_context.get() is None


@pytest.mark.parametrize(
    "model", [WorkCreate, InstanceCreate, ItemCreate, AgentCreate, SubjectCreate]
)
def test_payload_validation_handler_in_isolation(model: type[BaseModel]) -> None:
    # No body endpoint exists in #45. Exercise Pydantic and the existing handler
    # directly; this is deliberately not claimed as catalog HTTP 422 coverage.
    with pytest.raises(ValidationError) as caught:
        model.model_validate({"secret-sentinel": "secret-sentinel"})
    identifier = str(uuid4())
    request = Request({"type": "http", "state": {"request_id": identifier}})
    response = asyncio.run(
        handle_request_validation(request, RequestValidationError(caught.value.errors()))
    )
    assert response.status_code == 422
    problem = json.loads(response.body)
    assert problem["code"] == "schema.invalid"
    assert problem["issues"]
    assert "secret-sentinel" not in response.body.decode()


@pytest.mark.parametrize(
    "path,origin,method,status",
    [
        ("/api/v1/identity/me", "http://localhost:3000", "GET", 200),
        ("/api/v1/identity/me", "https://denied.example.invalid", "GET", 400),
        ("/api/v1/identity/me", "http://localhost:3000", "PATCH", 400),
        ("/api/v1/catalog/works", "http://localhost:3000", "GET", 200),
    ],
)
def test_cors_is_not_expanded_for_future_endpoints(
    path: str,
    origin: str,
    method: str,
    status: int,
) -> None:
    settings = Settings(_env_file=None, app_env="test", cors_origins=["http://localhost:3000"])
    with TestClient(main.create_app(settings)) as client:
        response = client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Authorization, X-Request-ID",
            },
        )
    assert response.status_code == status
    assert response.headers["x-frame-options"] == "DENY"
    UUID(response.headers["x-request-id"])
    if status == 200:
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-methods"] == (
            "GET, POST" if path == "/api/v1/catalog/works" else "GET"
        )
        assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("collection", ["works", "instances", "items", "agents", "subjects"])
@pytest.mark.parametrize("method", ["POST", "GET", "PATCH"])
def test_catalog_authentication_and_denial_before_storage(collection: str, method: str) -> None:
    """Dependency overrides prove isolated authorization, not real OIDC integration."""
    app = main.create_app(Settings(_env_file=None, app_env="test", oidc_enabled=False))
    path = f"/api/v1/catalog/{collection}"
    if method != "POST":
        path += f"/{uuid4()}"
    from pathlib import Path

    names = dict(
        zip(
            ["works", "instances", "items", "agents", "subjects"],
            ["work", "instance", "item", "agent", "subject"],
            strict=True,
        )
    )
    payload = (
        json.loads(
            (
                Path(__file__).parents[1]
                / "fixtures/bibliographic/contracts"
                / f"{names[collection]}-create.json"
            ).read_text()
        )
        if method == "POST"
        else {}
    )
    with TestClient(app) as client:
        for authorization in (None, "Bearer invalid", "Basic forged"):
            headers = {
                "If-Match": '"opaque"',
                "actor_id": "forged",
                "permissions": "system:admin",
            }
            if authorization is not None:
                headers["Authorization"] = authorization
            response = client.request(method, path, json=payload, headers=headers)
            assert response.status_code == 401
        app.dependency_overrides[get_current_actor] = lambda: actor(P.SYSTEM_ADMIN)
        with patch("sqlalchemy.ext.asyncio.AsyncSession.execute", side_effect=AssertionError):
            response = client.request(method, path, json=payload, headers={"If-Match": '"opaque"'})
        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("collection", ["works", "instances", "items", "agents", "subjects"])
def test_editorial_read_requires_both_permissions(collection: str) -> None:
    app = main.create_app(Settings(_env_file=None, app_env="test"))
    read = P.AUTHORITY_READ if collection in {"agents", "subjects"} else P.BIBLIOGRAPHIC_READ
    with TestClient(app) as client:
        for permissions in ((read,), (P.METADATA_REVISION_READ,), ()):
            app.dependency_overrides[get_current_actor] = actor_override(actor(*permissions))
            with patch(
                "sqlalchemy.ext.asyncio.AsyncSession.execute",
                side_effect=AssertionError,
            ):
                response = client.get(f"/api/v1/catalog/{collection}/{uuid4()}")
            assert response.status_code == 403


@pytest.mark.parametrize(
    "path,method,status",
    [
        ("/api/v1/catalog/works", "POST", 200),
        ("/api/v1/catalog/works", "GET", 200),
        ("/api/v1/catalog/works/00000000-0000-4000-8000-000000000001", "PATCH", 200),
        ("/api/v1/catalog/works/00000000-0000-4000-8000-000000000001", "DELETE", 400),
        ("/api/v1/catalog/works/validation", "POST", 200),
        ("/api/v1/catalog/profiles", "GET", 200),
    ],
)
def test_catalog_cors_exact_paths(path: str, method: str, status: int) -> None:
    app = main.create_app(Settings(_env_file=None, cors_origins=["http://localhost:3000"]))
    with TestClient(app) as client:
        response = client.options(
            path,
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": (
                    "Authorization, Content-Type, If-Match, X-Request-ID"
                ),
            },
        )
    assert response.status_code == status
    assert "access-control-allow-credentials" not in response.headers


def test_editing_etag_is_bound_to_representation_and_editorial_generation() -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    from libris.modules.bibliographic.api.representations import editing_etag
    from libris.modules.bibliographic.api.schemas import WorkRead
    from libris.modules.bibliographic.application.catalog import CatalogView
    from libris.modules.bibliographic.domain.models import Provenance, ResourceIdentity, Revision
    from libris.modules.bibliographic.infrastructure.validation import INSTITUTIONAL_PROFILE_ID

    identity = ResourceIdentity.create("https://synthetic.invalid/records")
    resource = WorkRead(
        internal_id=identity.internal_id,
        uri=identity.uri,
        title=[{"main_title": [{"value": "Synthetic"}]}],
    )
    revision = Revision(
        identity, 1, datetime.now(UTC), Provenance("test", "etag"), INSTITUTIONAL_PROFILE_ID
    )
    view = CatalogView(revision, resource, 1)
    etag = editing_etag(view)
    assert etag.startswith('"catalog-v1-') and etag.endswith('"')
    assert etag != revision.concurrency_token
    assert etag == editing_etag(view)
    assert etag != editing_etag(replace(view, editorial_version=2))
    assert etag != editing_etag(replace(view, revision=replace(revision, number=2)))
    assert etag != editing_etag(replace(view, editorial_version=None))
    assert etag != editing_etag(
        replace(
            view,
            resource=resource.model_copy(update={"note": []}),
            revision=replace(revision, profile="different"),
        )
    )


def test_storage_unavailable_problem_is_safe() -> None:
    app = main.create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_current_actor] = lambda: actor(
        P.BIBLIOGRAPHIC_READ, P.METADATA_REVISION_READ
    )
    with (
        TestClient(app) as client,
        patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute", side_effect=ConnectionError("secret SQL")
        ),
    ):
        response = client.get(f"/api/v1/catalog/works/{uuid4()}")
    assert response.status_code == 503
    assert response.json()["code"] == "infrastructure.unavailable"
    assert "secret SQL" not in response.text
    assert response.json()["request_id"] == response.headers["x-request-id"]
