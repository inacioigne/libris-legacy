"""M05.06 closed queries, scope-bound cursors, OIDC and representation contracts."""

import asyncio
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.bibliographic.api.schemas.listing import PublicCatalogQuery
from libris.modules.bibliographic.application.catalog_listing import (
    decode_cursor,
    encode_cursor,
    scope_digest,
)
from libris.modules.bibliographic.domain.errors import CatalogError
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.identity.api.dependencies import (
    get_current_actor,
    get_identity_service,
)
from libris.modules.identity.application.service import IdentityService
from libris.modules.identity.domain.permissions import Permission as P
from test_catalogographic_http import actor, actor_override

COLLECTIONS = ["works", "instances", "items", "agents", "subjects"]


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_listing_authorizes_before_storage(collection: str) -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    with (
        TestClient(app) as client,
        patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            side_effect=AssertionError("no SQL"),
        ),
    ):
        path = f"/api/v1/catalog/{collection}"
        assert client.get(path).status_code == 401
        for permissions in [
            (),
            (P.SYSTEM_ADMIN,),
            (P.BIBLIOGRAPHIC_READ, P.AUTHORITY_READ),
        ]:
            app.dependency_overrides[get_current_actor] = actor_override(actor(*permissions))
            response = client.get(path)
            assert response.status_code == 403
            assert response.headers["content-type"] == "application/problem+json"
        app.dependency_overrides.clear()
        for token in ["invalid", "expired.invalid.signature"]:
            response = client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize(
    "query",
    [
        "limit=-1",
        "limit=0",
        "limit=101",
        "limit=1.5",
        "limit=null",
        "internal_id=invalid",
        "uri=relative",
        "uri=null",
        "profile=",
        "cursor=",
        "include_drafts=true",
        "order=title",
        "actor_id=forged",
        "unknown=value",
        "editorial_state=invalid",
    ],
)
def test_closed_listing_query(public: bool, query: str) -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_current_actor] = actor_override(actor(*P))
    with TestClient(app) as client:
        response = client.get(f"/api/v1/catalog/{'public/' if public else ''}works?{query}")
        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"


def test_cursor_is_canonical_and_bound_to_scope_filters_and_limit() -> None:
    query = PublicCatalogQuery(limit=2, profile="profile-v1")
    scope = scope_digest(EntityType.WORK, True, query)
    identifier = uuid4()
    cursor = encode_cursor(identifier, scope)
    assert decode_cursor(cursor, scope) == identifier
    assert decode_cursor(None, scope) is None
    for value in ["%%%", "a" * 4000, cursor + "=", encode_cursor(UUID(int=1), "wrong")]:
        with pytest.raises(CatalogError):
            decode_cursor(value, scope)
    for entity, public, other in [
        (EntityType.AGENT, True, query),
        (EntityType.WORK, False, query),
        (EntityType.WORK, True, PublicCatalogQuery(limit=3, profile="profile-v1")),
        (EntityType.WORK, True, PublicCatalogQuery(limit=2, profile="profile-v2")),
    ]:
        with pytest.raises(CatalogError):
            decode_cursor(cursor, scope_digest(entity, public, other))


def test_listing_openapi_and_cors() -> None:
    app = create_app(Settings(_env_file=None, cors_origins=["http://localhost:3000"]))
    paths = app.openapi()["paths"]
    with TestClient(app) as client:
        for collection in COLLECTIONS:
            for prefix in ["", "public/"]:
                path = f"/api/v1/catalog/{prefix}{collection}"
                operation = paths[path]["get"]
                assert bool(operation.get("security")) == (not prefix)
                names = {p["name"] for p in operation["parameters"]}
                assert names == {"limit", "cursor", "internal_id", "uri", "profile"} | (
                    set() if prefix else {"editorial_state"}
                )
                response = client.options(
                    path,
                    headers={
                        "Origin": "http://localhost:3000",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert response.status_code == 200
            read = paths[f"/api/v1/catalog/{collection}/{{id}}"]["get"]
            assert set(read["responses"]["200"]["content"]) == {
                "application/json",
                "application/ld+json",
                "text/turtle",
            }
            assert "406" in read["responses"]


def test_listing_expired_signed_token_is_rejected() -> None:
    # Reuse the actual M04 validator and its offline JWKS provider.
    from identity.test_identity import Provider

    async def scenario() -> None:
        provider = Provider()
        app = create_app(provider.settings)
        app.dependency_overrides[get_identity_service] = lambda: IdentityService(
            provider.validator, "client", "libris-api"
        )
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/catalog/works",
                headers={
                    "Authorization": "Bearer " + provider.token({"exp": 1}),
                },
            )
            assert response.status_code == 401
        await provider.client.aclose()

    asyncio.run(scenario())
