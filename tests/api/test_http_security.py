"""HTTP controls at the real ASGI boundary; no database/provider required."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from libris.core.config import Settings
from libris.main import create_app


@pytest.mark.parametrize("origin", [
    "*", "null", "https://user:pass@example.com", "https://example.com/",
    "https://example.com/path", "https://example.com?q=1", "https://example.com#f",
    "https://example.com?", "https://example.com#", "https://example.com:",
    "https://example.com:0", "https://example.com:65536", "https://example.com:bad",
    "http://example.com", " https://example.com", "https://example.com\\evil",
    "https://*.example.com", "https://bad_host", "https://example.com\n",
])
def test_cors_invalid_origins_fail_closed(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


def test_cors_production_requires_https_and_empty_default_never_grants_access() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", cors_origins=["http://localhost:3000"])
    config = Settings(_env_file=None, app_env="production", cors_origins=["https://web.example"],
                      oidc_issuer_url="https://identity.example/realm")
    with TestClient(create_app(config)) as client:
        assert client.get("/docs").status_code == 404
    assert Settings(_env_file=None).cors_origins == []


@pytest.mark.parametrize(("origin", "method", "header", "status"), [
    ("http://localhost:3000", "GET", "Authorization, X-Request-ID", 200),
    ("https://evil.example", "GET", "Authorization", 400),
    ("null", "GET", "Authorization", 400),
    ("http://localhost:3000", "POST", "Authorization", 400),
    ("http://localhost:3000", "DELETE", "Authorization", 400),
    ("http://localhost:3000", "GET", "X-Permissions", 400),
])
def test_preflight(origin: str, method: str, header: str, status: int) -> None:
    app = create_app(Settings(_env_file=None, cors_origins=["http://localhost:3000"]))
    with TestClient(app) as client:
        response = client.options("/api/v1/identity/me", headers={
            "Origin": origin, "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": header,
        })
    assert response.status_code == status
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["access-control-allow-methods"] == "GET"
    if origin != "http://localhost:3000":
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/vocabularies", "GET"),
        ("/api/v1/vocabularies/00000000-0000-4000-8000-000000000001/concepts", "GET"),
        (
            "/api/v1/vocabularies/00000000-0000-4000-8000-000000000001/"
            "concepts/00000000-0000-4000-8000-000000000002",
            "GET",
        ),
        ("/api/v1/vocabularies/00000000-0000-4000-8000-000000000001/validate", "POST"),
    ],
)
def test_vocabulary_cors_preflight_includes_conditional_cache_header(
    path: str, method: str
) -> None:
    app = create_app(Settings(_env_file=None, cors_origins=["http://localhost:3000"]))
    with TestClient(app) as client:
        response = client.options(
            path,
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Authorization, If-None-Match",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-methods"] == method
    assert "if-none-match" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://localhost:3000"])
@pytest.mark.parametrize("authorization", [None, "Bearer invalid-secret-token"])
def test_401_private_and_cors_is_not_authentication(
    origin: str | None, authorization: str | None
) -> None:
    headers = {}
    if origin:
        headers["Origin"] = origin
    if authorization:
        headers["Authorization"] = authorization
    app = create_app(Settings(_env_file=None, cors_origins=["http://localhost:3000"]))
    with TestClient(app) as client:
        response = client.get("/api/v1/identity/me", headers=headers)
        assert client.get("/health").status_code == 200
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert "invalid-secret-token" not in response.text
    assert response.headers.get("access-control-allow-origin") == (
        origin if origin == "http://localhost:3000" else None
    )


@pytest.mark.parametrize(("environment", "scheme", "hsts"), [
    ("development", "http", False), ("production", "http", False),
    ("production", "https", True), ("development", "https", False),
])
def test_headers_and_hsts_use_trusted_asgi_scheme(
    environment: str, scheme: str, hsts: bool
) -> None:
    config = Settings(_env_file=None, app_env=environment,  # type: ignore[arg-type]
                      oidc_issuer_url="https://identity.example/realm")
    with TestClient(create_app(config), base_url=f"{scheme}://testserver") as client:
        response = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "camera=()" in response.headers["permissions-policy"]
    assert ("strict-transport-security" in response.headers) is hsts
    assert "cache-control" not in response.headers


def test_unexpected_error_retains_security_and_cors_without_leaking() -> None:
    app = create_app(Settings(_env_file=None, cors_origins=["https://web.example"]))

    @app.get("/failure")
    def failure() -> None:
        raise RuntimeError("private-access-token")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/failure", headers={"Origin": "https://web.example"})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "https://web.example"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json()["contract_version"] == "1.0"
    assert "private-access-token" not in response.text


def test_preflight_cannot_grant_nonexistent_endpoints() -> None:
    app = create_app(Settings(_env_file=None, cors_origins=["https://web.example"]))
    with TestClient(app) as client:
        response = client.options("/future/catalog", headers={
            "Origin": "https://web.example", "Access-Control-Request-Method": "GET",
        })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("issuer", [
    "https://identity.example:bad/realm", "https://identity.example:0/realm",
    "https://identity.example:/realm", "https://@identity.example/realm",
    "https://identity.example/realm\n", "https://identity.example\\evil/realm",
])
def test_invalid_issuer_configuration_is_rejected(issuer: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, oidc_issuer_url=issuer)


def test_production_rejects_local_issuer_default_even_when_oidc_disabled() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production")
