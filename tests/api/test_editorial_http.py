"""HTTP authorization/validation without a database; transactions tested separately."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.identity.api.dependencies import get_current_actor
from libris.modules.identity.domain.permissions import Permission as P
from test_catalogographic_http import actor, actor_override

COMMANDS = [
    "submit",
    "approve",
    "publish",
    "return",
    "withdraw",
    "republish",
    "archive",
]
QUERIES = [
    "editorial",
    "history",
    "audit",
    f"audit/{uuid4()}",
    "revisions",
    "revisions/1",
    "revisions/1/snapshot",
    "revisions/compare?previous=1&current=1",
]


@pytest.mark.parametrize(
    "collection", ["works", "instances", "items", "agents", "subjects"]
)
@pytest.mark.parametrize(
    "suffix", QUERIES + ["editorial/" + cmd for cmd in COMMANDS] + ["revisions/restore"]
)
def test_editorial_anonymous_and_insufficient_permissions_before_storage(
    collection: str,
    suffix: str,
) -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    path = f"/api/v1/catalog/{collection}/{uuid4()}/{suffix}"
    mutable = suffix.startswith("editorial/") or suffix == "revisions/restore"
    method = "POST" if mutable else "GET"
    body = (
        {"source_revision": 1}
        if suffix == "revisions/restore"
        else {"selected_revision": 1}
    )
    with (
        TestClient(app) as client,
        patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            side_effect=AssertionError("no storage"),
        ),
    ):
        response = client.request(
            method, path, json=body, headers={"If-Match": '"tag"'}
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        for permissions in [(), (P.SYSTEM_ADMIN,), (P.BIBLIOGRAPHIC_UPDATE,)]:
            app.dependency_overrides[get_current_actor] = actor_override(
                actor(*permissions)
            )
            response = client.request(
                method, path, json=body, headers={"If-Match": '"tag"'}
            )
            assert response.status_code == 403, response.text
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json()["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize(
    "extra", ["actor_id", "roles", "permissions", "expected_version"]
)
def test_transition_rejects_client_identity_and_expectations(
    command: str, extra: str
) -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_current_actor] = lambda: actor(*P)
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/catalog/works/{uuid4()}/editorial/{command}",
            json={"selected_revision": 1, extra: "sentinel-forged"},
            headers={"If-Match": '"tag"'},
        )
    assert response.status_code == 422
    assert "sentinel-forged" not in response.text


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=201",
        "after_time=2026-10-16T00:00:00",
        "after_event_id=invalid",
        f"after_event_id={uuid4()}",
        "after_time=2026-10-16T00:00:00Z",
    ],
)
def test_event_cursor_input_is_bounded_and_timezone_aware(query: str) -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_current_actor] = lambda: actor(*P)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/catalog/works/{uuid4()}/audit?{query}")
    assert response.status_code == 422


@pytest.mark.parametrize(
    "suffix,method",
    [
        ("editorial", "GET"),
        ("editorial/publish", "POST"),
        ("editorial/republish", "POST"),
        ("public", "GET"),
        ("revisions/1/snapshot", "GET"),
        ("revisions/restore", "POST"),
        ("revisions/compare", "GET"),
        ("history", "GET"),
        ("audit", "GET"),
    ],
)
def test_editorial_cors_allows_only_implemented_methods(
    suffix: str, method: str
) -> None:
    app = create_app(Settings(_env_file=None, cors_origins=["http://localhost:3000"]))
    with TestClient(app) as client:
        for requested, expected in [(method, 200), ("DELETE", 400)]:
            response = client.options(
                f"/api/v1/catalog/works/{uuid4()}/{suffix}",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": requested,
                    "Access-Control-Request-Headers": "Authorization, If-Match",
                },
            )
            assert response.status_code == expected


def test_editorial_openapi_contract() -> None:
    schema = create_app(Settings(_env_file=None)).openapi()
    paths = schema["paths"]
    base = "/api/v1/catalog/{collection}/{id}"
    assert "security" not in paths[base + "/public"]["get"]
    for command in COMMANDS:
        op = paths[base + "/editorial/" + command]["post"]
        assert op["security"] == [{"HTTPBearer": []}]
        assert any(p["name"] == "if-match" for p in op["parameters"])
        assert set(op["responses"]) >= {
            "200",
            "400",
            "401",
            "403",
            "409",
            "412",
            "422",
            "428",
        }
    assert (
        "text/turtle"
        in paths[base + "/revisions/{number}/snapshot"]["get"]["responses"]["200"][
            "content"
        ]
    )
