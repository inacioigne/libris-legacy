import asyncio
import json
import time
from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.identity.api.dependencies import (
    get_current_actor,
    require_permissions,
)
from libris.modules.identity.application.service import IdentityService, authorize
from libris.modules.identity.domain.exceptions import (
    AuthenticationError,
    PermissionDenied,
)
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.domain.permissions import Permission as P
from libris.modules.identity.infrastructure.oidc import OIDCValidator
from libris.modules.identity.infrastructure.role_mapper import (
    extract_roles,
    map_permissions,
)
from pydantic import ValidationError


class Provider:
    def __init__(self) -> None:
        self.settings = Settings(_env_file=None, oidc_enabled=True, oidc_clock_skew_seconds=0)
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.keys = [self.jwk(self.private, "one")]
        self.calls: list[str] = []
        self.now = 1000.0
        self.status = 200
        self.invalid_keys = False
        self.timeout = False
        self.discovery_issuer = self.settings.oidc_issuer_url
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.validator = OIDCValidator(self.settings, self.client, lambda: self.now)

    @staticmethod
    def jwk(private: rsa.RSAPrivateKey, kid: str) -> dict[str, object]:
        value: dict[str, object] = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key())
        )
        return {**value, "kid": kid, "use": "sig", "alg": "RS256"}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.timeout:
            raise httpx.ReadTimeout("timeout")
        if request.url.path.endswith("openid-configuration"):
            document: object = {
                "issuer": self.discovery_issuer,
                "jwks_uri": "http://provider/keys",
            }
        else:
            document = {"keys": "invalid" if self.invalid_keys else self.keys}
        return httpx.Response(self.status, json=document)

    def token(
        self,
        changes: dict[str, object] | None = None,
        *,
        omit: str | None = None,
        private: rsa.RSAPrivateKey | None = None,
        kid: str | None = "one",
        headers: dict[str, object] | None = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": self.settings.oidc_issuer_url,
            "sub": "subject",
            "aud": "libris-api",
            "exp": now + 600,
            "iat": now - 1,
            "typ": "Bearer",
            "azp": "libris-web",
            "resource_access": {"libris-api": {"roles": ["libris-cataloger"]}},
        }
        claims.update(changes or {})
        if omit:
            claims.pop(omit)
        return jwt.encode(
            claims,
            private or self.private,
            algorithm="RS256",
            headers={**({"kid": kid} if kid is not None else {}), **(headers or {})},
        )


@pytest.fixture
def provider() -> Iterator[Provider]:
    value = Provider()
    yield value
    asyncio.run(value.client.aclose())


def test_valid_token_and_actor(provider: Provider) -> None:
    service = IdentityService(provider.validator, "client", "libris-api")
    actor = asyncio.run(service.authenticate(provider.token()))
    assert actor.actor_id == actor_identifier(provider.settings.oidc_issuer_url, "subject")
    assert P.BIBLIOGRAPHIC_CREATE in actor.permissions
    assert P.SYSTEM_ADMIN not in actor.permissions
    with pytest.raises(FrozenInstanceError):
        actor.actor_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes,omit",
    [
        ({"exp": 1}, None),
        ({"iss": "https://wrong"}, None),
        ({"aud": "libris-web"}, None),
        ({}, "aud"),
        ({"aud": []}, None),
        ({"sub": ""}, None),
        ({"sub": " "}, None),
        ({}, "sub"),
        ({}, "exp"),
        ({"nbf": time.time() + 600}, None),
        ({"iat": time.time() + 600}, None),
        ({"typ": "ID"}, None),
        ({}, "typ"),
        ({"exp": "9999999999"}, None),
        ({"exp": True}, None),
        ({"exp": float("nan")}, None),
        ({}, "iss"),
        ({"aud": 42}, None),
        *[
            ({name: value}, None)
            for name in ("exp", "nbf", "iat")
            for value in (None, True, "9999999999", [], {}, float("inf"), float("-inf"))
        ],
        ({"nbf": float("nan")}, None),
        ({"iat": float("nan")}, None),
    ],
)
def test_invalid_claims(provider: Provider, changes: dict[str, object], omit: str | None) -> None:
    with pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(provider.token(changes, omit=omit)))


def test_signed_non_string_issuer(provider: Provider) -> None:
    # PyJWT's high-level encoder refuses this claim before the validator sees it.
    claims = jwt.decode(provider.token(), options={"verify_signature": False})
    claims["iss"] = 42
    token = jwt.api_jws.encode(
        json.dumps(claims).encode(), provider.private, algorithm="RS256", headers={"kid": "one"}
    )
    with pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(token))


def test_signature_and_manipulated_roles(provider: Provider) -> None:
    token = provider.token(
        {"resource_access": {"libris-api": {"roles": ["libris-admin"]}}},
        private=provider.other,
    )
    with pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(token))


@pytest.mark.parametrize("token", ["broken", "a.b.c", "", "x" * 16385])
def test_malformed(provider: Provider, token: str) -> None:
    with pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(token))
    assert provider.calls == []


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_disallowed_algorithm(provider: Provider, algorithm: str) -> None:
    token = jwt.encode(
        {"sub": "s"},
        "x" * 32 if algorithm == "HS256" else None,
        algorithm=algorithm,
        headers={"kid": "one"},
    )
    with pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(token))
    assert not provider.calls


def test_cache_expiration_rotation_and_refresh_limit(provider: Provider) -> None:
    async def scenario() -> None:
        token = provider.token(headers={"jku": "http://attacker/keys"})
        await provider.validator.validate(token)
        await provider.validator.validate(token)
        assert len(provider.calls) == 2
        for _ in range(20):
            with pytest.raises(AuthenticationError):
                await provider.validator.validate(provider.token(kid="unknown"))
        assert len(provider.calls) == 2
        provider.now += 11
        provider.keys.append(provider.jwk(provider.other, "two"))
        await provider.validator.validate(provider.token(private=provider.other, kid="two"))
        assert len(provider.calls) == 3
        provider.now += 301
        await provider.validator.validate(token)
        assert len(provider.calls) == 4

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["status", "timeout", "invalid_keys", "issuer"])
def test_provider_failure_and_backoff(provider: Provider, failure: str) -> None:
    if failure == "status":
        provider.status = 503
    elif failure == "issuer":
        provider.discovery_issuer = "http://wrong"
    else:
        setattr(provider, failure, True)

    async def scenario() -> None:
        for _ in range(10):
            with pytest.raises(AuthenticationError):
                await provider.validator.validate(provider.token())
        assert len(provider.calls) <= 2

    asyncio.run(scenario())


def test_no_stale_keys_after_expiration(provider: Provider) -> None:
    async def scenario() -> None:
        await provider.validator.validate(provider.token())
        provider.now += 301
        provider.status = 503
        with pytest.raises(AuthenticationError):
            await provider.validator.validate(provider.token())

    asyncio.run(scenario())


def test_unknown_key_after_refresh(provider: Provider) -> None:
    with pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(provider.token(kid="missing")))
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    "roles,expected",
    [
        (frozenset(), frozenset()),
        (frozenset({"unknown"}), frozenset()),
        (
            frozenset({"libris-reader"}),
            frozenset({P.BIBLIOGRAPHIC_READ, P.AUTHORITY_READ}),
        ),
        (frozenset({"libris-admin"}), frozenset(P)),
    ],
)
def test_role_mapping(roles: frozenset[str], expected: frozenset[P]) -> None:
    assert map_permissions(roles) == expected


def test_union_and_trusted_role_source() -> None:
    assert map_permissions(frozenset({"libris-reader", "libris-cataloger", "unknown"})) == (
        map_permissions(frozenset({"libris-cataloger"}))
    )
    claims: dict[str, object] = {
        "realm_access": {"roles": ["libris-admin"]},
        "resource_access": {"other-client": {"roles": ["libris-admin"]}},
    }
    assert extract_roles(claims, "client", "libris-api") == frozenset()
    assert extract_roles(claims, "realm", "libris-api") == frozenset({"libris-admin"})
    assert extract_roles({"realm_access": {"roles": "libris-admin"}}, "realm", "x") == frozenset()
    assert extract_roles({"realm_access": {"roles": [1]}}, "realm", "x") == frozenset()


def test_identifier_stability(provider: Provider) -> None:
    assert actor_identifier("issuer", "subject") == actor_identifier("issuer", "subject")
    assert actor_identifier("issuer", "subject") != actor_identifier("other", "subject")
    assert actor_identifier("ab", "c") != actor_identifier("a", "bc")

    async def scenario() -> None:
        first = IdentityService(provider.validator, "client", "libris-api")
        second = IdentityService(provider.validator, "client", "libris-api")
        a = await first.authenticate(provider.token({"name": "A", "email": "a@example.invalid"}))
        b = await second.authenticate(provider.token({"name": "B", "email": "b@example.invalid"}))
        assert a.actor_id == b.actor_id

    asyncio.run(scenario())


def test_fastapi_authentication_authorization_and_override(provider: Provider) -> None:
    app = create_app(provider.settings)

    @app.get("/test-protected")
    def protected(
        actor: AuthenticatedActor = Depends(require_permissions(P.BIBLIOGRAPHIC_CREATE)),
    ) -> str:
        return actor.actor_id

    @app.get("/test-admin")
    def admin(
        actor: AuthenticatedActor = Depends(require_permissions(P.SYSTEM_ADMIN)),
    ) -> str:
        return actor.actor_id

    with TestClient(app) as client:
        app.state.identity_service = IdentityService(provider.validator, "client", "libris-api")
        for headers in (
            {},
            {"Authorization": "Basic abc"},
            {"Authorization": "Bearer invalid"},
        ):
            response = client.get("/api/v1/identity/me", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
        headers = {"Authorization": "Bearer " + provider.token()}
        response = client.get("/api/v1/identity/me", headers=headers)
        assert response.status_code == 200
        assert set(response.json()) == {"actor_id", "roles", "permissions"}
        assert client.get("/test-protected", headers=headers).status_code == 200
        assert client.get("/test-admin", headers=headers).status_code == 403
        no_roles = {"Authorization": "Bearer " + provider.token({"resource_access": {}})}
        assert client.get("/test-protected", headers=no_roles).status_code == 403
        actor = AuthenticatedActor("test", "issuer", "subject", frozenset(), frozenset(P))
        app.dependency_overrides[get_current_actor] = lambda: actor
        assert client.get("/test-admin").status_code == 200
        assert client.get("/api/v1/identity/me").json()["actor_id"] == "test"
        authorize(actor, (P.SYSTEM_ADMIN,))
        with pytest.raises(PermissionDenied):
            authorize(
                AuthenticatedActor("t", "i", "s", frozenset(), frozenset()),
                (P.SYSTEM_ADMIN,),
            )


def test_disabled_never_bypasses_authentication() -> None:
    with TestClient(create_app(Settings(_env_file=None, oidc_enabled=False))) as client:
        assert (
            client.get("/api/v1/identity/me", headers={"Authorization": "Bearer abc"}).status_code
            == 401
        )
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "config",
    [
        {"oidc_algorithms": "none"},
        {"oidc_algorithms": "HS256"},
        {"oidc_algorithms": ""},
        {"oidc_jwks_cache_ttl": 0},
        {"oidc_issuer_url": "http://user:pass@host"},
        {
            "oidc_enabled": True,
            "app_env": "production",
            "oidc_issuer_url": "http://host",
        },
    ],
)
def test_settings_fail_closed(config: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **config)


def test_concurrent_initial_requests_share_refresh(provider: Provider) -> None:
    async def scenario() -> None:
        await asyncio.gather(*(provider.validator.validate(provider.token()) for _ in range(10)))
        assert len(provider.calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation",
    ["missing-kid", "encryption", "wrong-alg", "bad-key", "duplicate", "key-ops"],
)
def test_incompatible_keys(provider: Provider, mutation: str) -> None:
    token = provider.token()
    if mutation == "missing-kid":
        token = provider.token(kid=None)
    elif mutation == "encryption":
        provider.keys[0]["use"] = "enc"
    elif mutation == "wrong-alg":
        provider.keys[0]["alg"] = "RS512"
    elif mutation == "bad-key":
        provider.keys[0]["n"] = ""
    elif mutation == "key-ops":
        provider.keys[0]["key_ops"] = ["sign"]
    else:
        provider.keys.append(dict(provider.keys[0]))
    with pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(token))


def test_expiration_infinity(provider: Provider) -> None:
    with pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(provider.token({"exp": float("inf")})))


def test_bearer_cors_preflight() -> None:
    app = create_app(Settings(_env_file=None, cors_origins=["http://localhost:3000"]))
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/identity/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    "case",
    [
        "aud-missing",
        "aud-wrong",
        "issuer",
        "expired",
        "id-token",
        "typ-missing",
        "signature",
        "algorithm",
        "malformed",
    ],
)
def test_negative_tokens_have_http_bearer_challenge(provider: Provider, case: str) -> None:
    changes: dict[str, object] = {}
    omit = None
    if case == "aud-missing":
        omit = "aud"
    elif case == "aud-wrong":
        changes["aud"] = "libris-web"
    elif case == "issuer":
        changes["iss"] = "http://wrong"
    elif case == "expired":
        changes["exp"] = 1
    elif case == "id-token":
        changes["typ"] = "ID"
    elif case == "typ-missing":
        omit = "typ"
    token = provider.token(changes, omit=omit)
    if case == "signature":
        token = provider.token(private=provider.other)
    elif case == "algorithm":
        token = jwt.encode({"sub": "s"}, "x" * 32, algorithm="HS256", headers={"kid": "one"})
    elif case == "malformed":
        token = "broken"
    app = create_app(provider.settings)
    with TestClient(app) as client:
        app.state.identity_service = IdentityService(provider.validator, "client", "libris-api")
        response = client.get("/api/v1/identity/me", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert token not in response.text


@pytest.mark.parametrize("roles", [[], ["unknown"]])
def test_no_privilege_authenticates_me_but_cannot_write(
    provider: Provider, roles: list[str]
) -> None:
    app = create_app(provider.settings)

    @app.get("/permission-probe")
    def probe(
        actor: AuthenticatedActor = Depends(require_permissions(P.SYSTEM_ADMIN)),
    ) -> str:
        return actor.actor_id

    claims: dict[str, object] = {
        "resource_access": {
            "libris-api": {"roles": roles},
            "other": {"roles": ["libris-admin"]},
        },
        "realm_access": {"roles": ["libris-admin"]},
    }
    headers = {
        "Authorization": "Bearer " + provider.token(claims),
        "X-Roles": "libris-admin",
        "X-Actor-ID": "fake",
        "X-Permissions": "system:admin",
    }
    with TestClient(app) as client:
        app.state.identity_service = IdentityService(provider.validator, "client", "libris-api")
        response = client.get("/api/v1/identity/me", headers=headers)
        assert response.status_code == 200
        assert response.json()["permissions"] == []
        assert response.json()["roles"] == roles
        assert response.json()["actor_id"] != "fake"
        assert client.get("/permission-probe", headers=headers).status_code == 403


@pytest.mark.parametrize(
    "failure",
    [
        "discovery-json",
        "discovery-shape",
        "missing-uri",
        "jwks-json",
        "jwks-shape",
        "too-many-keys",
        "oversize",
        "redirect",
    ],
)
def test_invalid_provider_documents_fail_closed(provider: Provider, failure: str) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        provider.calls.append(request.url.path)
        discovery = request.url.path.endswith("openid-configuration")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "http://untrusted/keys"})
        if failure == "oversize":
            return httpx.Response(200, content=b" " * 1_048_577)
        if failure == ("discovery-json" if discovery else "jwks-json"):
            return httpx.Response(200, content=b"{")
        if failure == ("discovery-shape" if discovery else "jwks-shape"):
            return httpx.Response(200, json=[])
        if discovery:
            value: dict[str, object] = {"issuer": provider.settings.oidc_issuer_url}
            if failure != "missing-uri":
                value["jwks_uri"] = "http://provider/keys"
        else:
            value = {"keys": provider.keys * (101 if failure == "too-many-keys" else 1)}
        return httpx.Response(200, json=value)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            validator = OIDCValidator(provider.settings, client, lambda: provider.now)
            for _ in range(10):
                with pytest.raises(AuthenticationError):
                    await validator.validate(provider.token())
            assert len(provider.calls) <= 2
            assert not any("untrusted" in call for call in provider.calls)

    asyncio.run(scenario())


def test_refresh_failure_recovery_and_removed_key(provider: Provider) -> None:
    async def scenario() -> None:
        original = provider.token()
        await provider.validator.validate(original)
        provider.now += 301
        provider.timeout = True
        for _ in range(10):
            with pytest.raises(AuthenticationError):
                await provider.validator.validate(original)
        assert len(provider.calls) == 3
        provider.timeout = False
        # Recovery remains throttled even after the provider becomes available.
        with pytest.raises(AuthenticationError):
            await provider.validator.validate(original)
        provider.now += 11
        provider.keys = [provider.jwk(provider.other, "two")]
        await provider.validator.validate(provider.token(private=provider.other, kid="two"))
        with pytest.raises(AuthenticationError):
            await provider.validator.validate(original)
        assert len(provider.calls) == 4

    asyncio.run(scenario())


def test_logs_do_not_disclose_token_or_claims(
    provider: Provider, caplog: pytest.LogCaptureFixture
) -> None:
    token = provider.token(
        {"sub": "private-subject", "email": "private@example.invalid"},
        private=provider.other,
    )
    with caplog.at_level("DEBUG"), pytest.raises(AuthenticationError):
        asyncio.run(provider.validator.validate(token))
    for sensitive in (token, "private-subject", "private@example.invalid"):
        assert sensitive not in caplog.text


def test_keycloak_realm_preserves_resource_server_contract() -> None:
    from pathlib import Path

    from libris.modules.identity.infrastructure.role_mapper import ROLE_PERMISSIONS

    realm = json.loads(
        (
            Path(__file__).resolve().parents[3] / "infrastructure/keycloak/libris-realm.json"
        ).read_text()
    )
    assert {role["name"] for role in realm["roles"]["client"]["libris-api"]} == set(
        ROLE_PERMISSIONS
    )
    assert not realm.get("users")
    assert realm["adminEventsEnabled"] is True
    assert realm["adminEventsDetailsEnabled"] is False
    assert len(realm["clients"]) == 2  # No externally accepted service client yet.
    web = next(client for client in realm["clients"] if client["clientId"] == "libris-web")
    assert "basic" in web["defaultClientScopes"]  # Keycloak 25+ maps required sub here.
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    assert web["redirectUris"] == ["http://localhost:3000/oidc/callback"]
    assert web["standardFlowEnabled"] and web["publicClient"]
    assert not web["implicitFlowEnabled"] and not web["directAccessGrantsEnabled"]
    assert web["serviceAccountsEnabled"] is False
    audience = next(
        mapper
        for mapper in web["protocolMappers"]
        if mapper["protocolMapper"] == "oidc-audience-mapper"
    )
    assert audience["config"]["included.client.audience"] == "libris-api"
    assert audience["config"]["access.token.claim"] == "true"
    assert audience["config"]["id.token.claim"] == "false"


@pytest.mark.parametrize("roles", [[], ["unknown"], ["libris-reader"], ["libris-cataloger"]])
def test_operation_guard_ignores_forged_http_authority(
    provider: Provider, roles: list[str]
) -> None:
    from typing import Annotated

    from fastapi import Body
    from libris.modules.identity.api.dependencies import get_execution_context
    from libris.modules.identity.application.authorization import (
        AuthorizationGuard,
        HumanContext,
    )
    from libris.modules.identity.domain.authorization import (
        Operation,
        ResourceContext,
        ResourceKind,
    )

    app = create_app(provider.settings)
    guard = AuthorizationGuard()

    @app.post("/test-operation")
    def protected(
        context: Annotated[HumanContext, Depends(get_execution_context)],
        payload: Annotated[dict[str, object], Body()],
    ) -> dict[str, str]:
        guard.require(
            context=context,
            operation=Operation.BIBLIOGRAPHIC_CREATE,
            resource=ResourceContext(ResourceKind.BIBLIOGRAPHIC),
        )
        return {"actor_id": context.actor.actor_id}

    with TestClient(app) as client:
        app.state.identity_service = IdentityService(provider.validator, "client", "libris-api")
        payload = {
            "actor_id": "forged",
            "roles": ["libris-admin"],
            "permissions": [p.value for p in P],
            "execution_actor": "service:admin",
        }
        spoof = {"X-Actor-ID": "forged", "X-Roles": "libris-admin", "X-Permissions": "system:admin"}
        for token in (None, "invalid", provider.token({"aud": "libris-web"})):
            headers = spoof | ({"Authorization": "Bearer " + token} if token else {})
            response = client.post("/test-operation", json=payload, headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
        token = provider.token({"resource_access": {"libris-api": {"roles": roles}}})
        response = client.post(
            "/test-operation?roles=libris-admin",
            json=payload,
            headers=spoof | {"Authorization": "Bearer " + token},
        )
        assert response.status_code == (200 if "libris-cataloger" in roles else 403)
        if response.status_code == 200:
            assert response.json()["actor_id"] == actor_identifier(
                provider.settings.oidc_issuer_url, "subject"
            )
        else:
            assert response.json()["contract_version"] == "1.0"
            assert response.json()["code"] == "authorization.denied"
        assert client.get("/health", headers=spoof).status_code == 200
        assert client.get("/api/v1/identity/me", headers=spoof).status_code == 401


@pytest.mark.parametrize("client", [None, "", "libris-service", "other-web", ["libris-web"], 42])
def test_only_signed_interactive_client_can_enter_human_boundary(
    provider: Provider, client: object
) -> None:
    app = create_app(provider.settings)
    token = provider.token(
        {
            "azp": client,
            "sub": "ordinary-subject-without-service-prefix",
            "resource_access": {"libris-api": {"roles": ["libris-admin"]}},
            "actor_id": "human:forged",
            "source": "interactive",
        },
        omit="azp" if client is None else None,
    )
    with TestClient(app) as http:
        app.state.identity_service = IdentityService(provider.validator, "client", "libris-api")
        response = http.get(
            "/api/v1/identity/me?azp=libris-web",
            headers={
                "Authorization": "Bearer " + token,
                "X-Client-ID": "libris-web",
                "X-Actor-ID": "human:forged",
                "X-Roles": "libris-admin",
            },
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["contract_version"] == "1.0"


def test_interactive_client_configuration_is_applied_by_lifespan(provider: Provider) -> None:
    settings = provider.settings.model_copy(update={"oidc_human_client_id": "institution-web"})
    app = create_app(settings)
    with TestClient(app):
        service = app.state.identity_service
        # Keep the real lifespan configuration; substitute only network/key retrieval.
        service.validator = provider.validator
        with pytest.raises(AuthenticationError):
            asyncio.run(service.authenticate(provider.token()))
        actor = asyncio.run(service.authenticate(provider.token({"azp": "institution-web"})))
        assert actor.actor_id == actor_identifier(settings.oidc_issuer_url, "subject")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, oidc_human_client_id=" ")
    with pytest.raises(ValueError):
        IdentityService(provider.validator, "client", "libris-api", "")


def test_role_revocation_affects_new_tokens_not_signed_access_tokens(provider: Provider) -> None:
    async def scenario() -> None:
        service = IdentityService(provider.validator, "client", "libris-api")
        before = provider.token()
        original = await service.authenticate(before)
        authorize(original, (P.BIBLIOGRAPHIC_CREATE,))
        # Represents a new issuance after external role removal; no JWT introspection.
        after = provider.token({"resource_access": {"libris-api": {"roles": []}}})
        revoked = await service.authenticate(after)
        assert revoked.actor_id == original.actor_id
        assert revoked.permissions == frozenset()
        with pytest.raises(PermissionDenied):
            authorize(revoked, (P.BIBLIOGRAPHIC_CREATE,))
        residual = await service.authenticate(before)
        assert residual == original
        authorize(residual, (P.BIBLIOGRAPHIC_CREATE,))
        expired = provider.token({"exp": 1})
        with pytest.raises(AuthenticationError):
            await service.authenticate(expired)

    asyncio.run(scenario())


def test_client_binding_requires_signature_and_does_not_classify_subject_prefix(
    provider: Provider,
) -> None:
    service = IdentityService(provider.validator, "client", "libris-api")
    with pytest.raises(AuthenticationError):
        forged = provider.token({"azp": "libris-web"}, private=provider.other)
        asyncio.run(service.authenticate(forged))
    # A subject is opaque; only the provider/client configuration defines trust.
    actor = asyncio.run(service.authenticate(provider.token({"sub": "service-account-human-name"})))
    assert actor.subject == "service-account-human-name"


@pytest.mark.parametrize("collection", ["works", "instances", "items", "agents", "subjects"])
@pytest.mark.parametrize(
    "case",
    ["missing", "malformed", "expired", "signature", "issuer", "audience", "key", "jwks"],
)
def test_catalog_rejects_invalid_oidc_before_storage(
    provider: Provider, collection: str, case: str
) -> None:
    """Real catalog handlers and JWT validation, deterministic offline discovery/JWKS."""
    from unittest.mock import patch

    changes = {
        "expired": {"exp": 1},
        "issuer": {"iss": "https://wrong.invalid"},
        "audience": {"aud": "libris-web"},
    }.get(case, {})
    token = provider.token(
        changes,
        private=provider.other if case == "signature" else None,
        kid="unknown" if case == "key" else "one",
    )
    if case == "malformed":
        token = "malformed-private-sentinel"
    if case == "jwks":
        provider.timeout = True
    headers = {
        "actor_id": "forged-private-sentinel",
        "roles": "libris-admin",
        "permissions": "system:admin",
    }
    if case != "missing":
        headers["Authorization"] = "Bearer " + token
    app = create_app(provider.settings)
    with TestClient(app) as client:
        app.state.identity_service.validator = provider.validator
        with patch("sqlalchemy.ext.asyncio.AsyncSession.execute", side_effect=AssertionError):
            response = client.post(f"/api/v1/catalog/{collection}", json={}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "authentication.required"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert "no-store" in response.headers["cache-control"]
    assert "etag" not in response.headers and "location" not in response.headers
    assert token not in response.text and "forged-private-sentinel" not in response.text


@pytest.mark.parametrize("collection", ["works", "instances", "items", "agents", "subjects"])
@pytest.mark.parametrize("roles", [[], ["libris-reader"], ["libris-publisher"]])
def test_catalog_signed_roles_cannot_be_promoted_by_client(
    provider: Provider, collection: str, roles: list[str]
) -> None:
    from pathlib import Path
    from unittest.mock import patch

    singular = dict(zip(
        ["works", "instances", "items", "agents", "subjects"],
        ["work", "instance", "item", "agent", "subject"], strict=True,
    ))[collection]
    payload = json.loads((
        Path(__file__).parents[2] / "fixtures/bibliographic/contracts"
        / f"{singular}-create.json"
    ).read_text())
    token = provider.token({
        "resource_access": {"libris-api": {"roles": roles}},
        "realm_access": {"roles": ["libris-admin"]},
        "permissions": [p.value for p in P],
        "actor_id": "forged-private-sentinel",
    })
    app = create_app(provider.settings)
    with TestClient(app) as client:
        app.state.identity_service.validator = provider.validator
        with patch("sqlalchemy.ext.asyncio.AsyncSession.execute", side_effect=AssertionError):
            response = client.post(
                f"/api/v1/catalog/{collection}?roles=libris-admin", json=payload,
                headers={"Authorization": "Bearer " + token, "X-Roles": "libris-admin"},
            )
    assert response.status_code == 403
    assert response.json()["code"] == "authorization.denied"
    assert "forged-private-sentinel" not in response.text and token not in response.text
