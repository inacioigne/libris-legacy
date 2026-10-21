"""Isolated Keycloak Code + PKCE probe. Credentials/tokens exist only in memory."""

import asyncio
import base64
import hashlib
import os
import secrets
import sys
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from fastapi import Depends
from fastapi.testclient import TestClient
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.identity.api.dependencies import require_permissions
from libris.modules.identity.domain.exceptions import AuthenticationError
from libris.modules.identity.domain.models import AuthenticatedActor
from libris.modules.identity.domain.permissions import Permission
from libris.modules.identity.infrastructure.oidc import OIDCValidator
from libris.modules.identity.infrastructure.role_mapper import (
    ROLE_PERMISSIONS,
    map_permissions,
)

ISSUER = os.environ.get("TEST_KEYCLOAK_ISSUER", "http://keycloak.localhost:8081/realms/libris")
CALLBACK = "http://localhost:3000/oidc/callback"


class ProbeFailure(Exception):
    """Messages contain only fixed stage names, never provider responses."""


def check(condition: bool, stage: str) -> None:
    if not condition:
        raise ProbeFailure(stage)


def challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def callback_code(location: str, state: str) -> str:
    parsed = urlsplit(location)
    check(f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == CALLBACK, "callback URI")
    query = parse_qs(parsed.query)
    states, codes = query.get("state", []), query.get("code", [])
    check(len(states) == 1 and secrets.compare_digest(states[0], state), "callback state")
    check(len(codes) == 1 and bool(codes[0]) and "error" not in query, "callback code")
    return codes[0]


class LoginForm(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action") or ""


def document(response: httpx.Response, stage: str) -> dict[str, object]:
    check(response.status_code == 200, stage)
    value: object = response.json()
    check(isinstance(value, dict), stage)
    if not isinstance(value, dict):
        raise ProbeFailure(stage)
    return value


def string(value: object, stage: str) -> str:
    check(isinstance(value, str) and bool(value), stage)
    if not isinstance(value, str):
        raise ProbeFailure(stage)
    return value


def loopback_probe_cookies(browser: httpx.Client, issuer: str) -> None:
    """Emulate secure loopback cookies only in the explicitly isolated HTTP probe."""
    parsed = urlsplit(issuer)
    if (
        os.environ.get("TEST_KEYCLOAK_ISOLATED") != "1"
        or parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "keycloak.localhost"}
    ):
        return
    for cookie in browser.cookies.jar:
        if cookie.domain == parsed.hostname and cookie.path == "/realms/libris/":
            cookie.secure = False


class KeycloakProbe:
    def __init__(self, client: httpx.Client, api_url: str) -> None:
        self.client = client
        self.api_url = api_url
        metadata = document(client.get(ISSUER + "/.well-known/openid-configuration"), "discovery")
        check(metadata.get("issuer") == ISSUER, "canonical issuer")
        self.authorization = string(metadata.get("authorization_endpoint"), "authorization URL")
        self.token_url = string(metadata.get("token_endpoint"), "token URL")
        for endpoint in (self.authorization, self.token_url):
            check(
                endpoint.startswith(ISSUER + "/protocol/openid-connect/"),
                "endpoint origin",
            )
        self.admin_url = ISSUER.replace("/realms/libris", "/admin/realms/libris")
        self.user_id: str | None = None
        self.key_id: str | None = None
        self.username = "m0402-probe-" + secrets.token_hex(12)
        self.password = secrets.token_urlsafe(32)
        self.admin_headers: dict[str, str] = {}

    def provision(self) -> None:
        admin = document(
            self.client.post(
                ISSUER.replace("/libris", "/master") + "/protocol/openid-connect/token",
                data={
                    "client_id": "admin-cli",
                    "grant_type": "password",
                    "username": os.environ["KEYCLOAK_ADMIN_USERNAME"],
                    "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"],
                },
            ),
            "temporary administrator authentication",
        )
        self.admin_headers = {
            "Authorization": "Bearer " + string(admin.get("access_token"), "administrator token")
        }
        response = self.client.post(
            self.admin_url + "/users",
            headers=self.admin_headers,
            json={
                "username": self.username,
                "enabled": True,
                "firstName": "Synthetic",
                "lastName": "Probe",
                "email": self.username + "@example.invalid",
                "emailVerified": True,
                "requiredActions": [],
                "credentials": [{"type": "password", "value": self.password, "temporary": False}],
            },
        )
        check(response.status_code == 201, "create owned temporary user")
        # Remember the ID before subsequent checks so finally can clean up failures.
        self.user_id = response.headers.get("location", "").rsplit("/", 1)[-1]
        check(bool(self.user_id), "temporary user ID")

    def assign(self, names: tuple[str, ...]) -> None:
        response = self.client.get(
            self.admin_url + "/clients",
            params={"clientId": "libris-api"},
            headers=self.admin_headers,
        )
        check(response.status_code == 200, "find API client")
        clients: object = response.json()
        check(isinstance(clients, list) and len(clients) == 1, "unique API client")
        if not isinstance(clients, list) or not isinstance(clients[0], dict):
            raise ProbeFailure("API client shape")
        client_id = string(clients[0].get("id"), "API client ID")
        roles = [
            document(
                self.client.get(
                    self.admin_url + f"/clients/{client_id}/roles/{name}",
                    headers=self.admin_headers,
                ),
                "provisioned client role",
            )
            for name in names
        ]
        response = self.client.post(
            self.admin_url + f"/users/{self.user_id}/role-mappings/clients/{client_id}",
            headers=self.admin_headers,
            json=roles,
        )
        check(response.status_code == 204, "explicit client role assignment")

    def code(self) -> tuple[str, str]:
        verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        # New cookie jar per login. Never follow the callback or print its URL.
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as browser:
            response = browser.get(
                self.authorization,
                params={
                    "client_id": "libris-web",
                    "response_type": "code",
                    "scope": "openid",
                    "redirect_uri": CALLBACK,
                    "state": state,
                    "code_challenge": challenge(verifier),
                    "code_challenge_method": "S256",
                },
            )
            check(response.status_code == 200, "authorization login page")
            form = LoginForm()
            form.feed(response.text)
            check(form.action.startswith(ISSUER + "/login-actions/"), "trusted login form")
            # Keycloak 26.8 treats .localhost as secure; stdlib CookieJar does not.
            # Emulate the browser loopback exception only in this HTTP dev probe.
            # Cookies remain private, and this is never used for a product session.
            loopback_probe_cookies(browser, ISSUER)
            response = browser.post(
                form.action,
                data={
                    "username": self.username,
                    "password": self.password,
                    "credentialId": "",
                },
            )
            check(
                response.status_code == 302,
                "provider login callback HTTP "
                + str(response.status_code)
                + "; invalid-credentials="
                + str("Invalid username or password" in response.text)
                + "; profile-action="
                + str("kc-update-profile-form" in response.text),
            )
            return callback_code(response.headers.get("location", ""), state), verifier

    def exchange(self, code: str, verifier: str) -> httpx.Response:
        return self.client.post(
            self.token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": "libris-web",
                "redirect_uri": CALLBACK,
                "code": code,
                "code_verifier": verifier,
            },
        )

    def cleanup(self) -> None:
        try:
            if self.key_id:
                response = self.client.delete(
                    self.admin_url + "/components/" + self.key_id, headers=self.admin_headers
                )
                check(response.status_code == 204, "delete owned temporary key provider")
                self.key_id = None
                print("PASS: owned temporary key provider removed")
        finally:
            self.cleanup_user()

    def cleanup_user(self) -> None:
        if self.user_id:
            response = self.client.delete(
                self.admin_url + "/users/" + self.user_id, headers=self.admin_headers
            )
            check(response.status_code == 204, "delete owned temporary user")
            self.user_id = None
            print("PASS: owned temporary user removed")

    async def rotation(self, original: str) -> None:
        current = [1000.0]
        requests: list[str] = []

        async def record(request: httpx.Request) -> None:
            requests.append(request.url.path)

        settings = Settings(_env_file=None, oidc_enabled=True, oidc_issuer_url=ISSUER)
        async with httpx.AsyncClient(trust_env=False, event_hooks={"request": [record]}) as client:
            validator = OIDCValidator(settings, client, lambda: current[0])
            await validator.validate(original)
            original_kid = jwt.get_unverified_header(original).get("kid")
            realm = document(
                self.client.get(self.admin_url, headers=self.admin_headers), "realm ID"
            )
            response = self.client.post(
                self.admin_url + "/components",
                headers=self.admin_headers,
                json={
                    "name": self.username + "-rsa",
                    "parentId": string(realm.get("id"), "realm ID"),
                    "providerId": "rsa-generated",
                    "providerType": "org.keycloak.keys.KeyProvider",
                    "config": {
                        "priority": ["1000"],
                        "enabled": ["true"],
                        "active": ["true"],
                        "algorithm": ["RS256"],
                        "keySize": ["2048"],
                    },
                },
            )
            check(response.status_code == 201, "create owned RSA key provider")
            self.key_id = response.headers.get("location", "").rsplit("/", 1)[-1]
            check(bool(self.key_id), "owned key provider ID")
            code, verifier = self.code()
            tokens = document(self.exchange(code, verifier), "rotated key token")
            rotated = string(tokens.get("access_token"), "rotated access token")
            check(jwt.get_unverified_header(rotated).get("kid") != original_kid, "new signing kid")
            try:
                await validator.validate(rotated)
            except AuthenticationError:
                pass
            else:
                raise ProbeFailure("new kid must wait for minimum refresh interval")
            check(len(requests) == 2, "no premature JWKS refresh")
            # Inject monotonic cache time; JWT dates still use the real wall clock.
            # Tokens/JWKS and rotation are real. No sleep or cryptographic bypass.
            current[0] += settings.oidc_jwks_refresh_interval
            await validator.validate(rotated)
            check(len(requests) == 3, "new key obtained with one JWKS refresh")
            await validator.validate(original)
            print("PASS: real RSA rotation; new kid throttled then accepted after JWKS refresh")

    def run(self) -> None:
        self.provision()
        self.assign(("libris-reader",))
        code, verifier = self.code()
        tokens = document(self.exchange(code, verifier), "Code + PKCE S256 exchange")
        access = string(tokens.get("access_token"), "access token")
        identity = string(tokens.get("id_token"), "ID token")
        settings = Settings(_env_file=None, oidc_enabled=True, oidc_issuer_url=ISSUER)

        async def validate() -> None:
            async with httpx.AsyncClient(trust_env=False) as client:
                claims = await OIDCValidator(settings, client).validate(access)
                check(claims.get("typ") == "Bearer", "signed Bearer discriminator")

        asyncio.run(validate())
        print("PASS: discovery/JWKS, RS256, issuer, audience and signed typ=Bearer")
        check(
            self.exchange(code, verifier).status_code == 400,
            "authorization code reuse rejected",
        )
        bad_code, _ = self.code()
        check(
            self.exchange(bad_code, secrets.token_urlsafe(48)).status_code == 400,
            "incorrect verifier rejected",
        )
        rejected = self.client.get(
            self.authorization,
            params={
                "client_id": "libris-web",
                "response_type": "code",
                "scope": "openid",
                "redirect_uri": "http://example.invalid/callback",
                "state": secrets.token_urlsafe(32),
                "code_challenge": challenge(verifier),
                "code_challenge_method": "S256",
            },
        )
        check(
            rejected.status_code == 400 and "location" not in rejected.headers,
            "arbitrary redirect rejected",
        )
        print("PASS: PKCE S256, verified state, wrong verifier, code reuse and arbitrary redirect")
        check(
            self.client.get(self.api_url + "/health").status_code == 200,
            "public health",
        )
        for token in (None, "malformed", identity):
            response = self.client.get(
                self.api_url + "/api/v1/identity/me",
                headers={"Authorization": "Bearer " + token} if token else {},
            )
            check(response.status_code == 401, "deployed API rejects invalid/ID token")
            check(response.headers.get("www-authenticate") == "Bearer", "Bearer challenge")
        me = document(
            self.client.get(
                self.api_url + "/api/v1/identity/me",
                headers={"Authorization": "Bearer " + access},
            ),
            "real access token authenticates deployed API",
        )
        check(me.get("roles") == ["libris-reader"], "least privilege reader")
        check(
            me.get("permissions") == sorted(p.value for p in ROLE_PERMISSIONS["libris-reader"]),
            "central permission map",
        )
        print("PASS: deployed API /health 200, /me 200 and invalid/ID tokens 401 + Bearer")
        # Permission-only route exists on an in-process test app, never on the deployed API.
        app = create_app(settings)

        @app.get("/probe-permission")
        def protected(
            actor: AuthenticatedActor = Depends(
                require_permissions(Permission.BIBLIOGRAPHIC_CREATE)
            ),
        ) -> str:
            return "authorized"

        with TestClient(app) as api:
            check(
                api.get(
                    "/probe-permission", headers={"Authorization": "Bearer " + access}
                ).status_code
                == 403,
                "real reader permission denial",
            )
            self.assign(("libris-cataloger", "libris-reviewer", "libris-publisher"))
            code, verifier = self.code()
            tokens = document(self.exchange(code, verifier), "multiple role token")
            access = string(tokens.get("access_token"), "multiple role access token")
            headers = {"Authorization": "Bearer " + access}
            check(
                api.get("/probe-permission", headers=headers).status_code == 200,
                "real cataloger permission granted",
            )
            me = document(
                self.client.get(self.api_url + "/api/v1/identity/me", headers=headers),
                "multiple roles deployed API",
            )
            roles = frozenset(
                {
                    "libris-reader",
                    "libris-cataloger",
                    "libris-reviewer",
                    "libris-publisher",
                }
            )
            check(me.get("roles") == sorted(roles), "signed multiple roles")
            check(
                me.get("permissions") == sorted(p.value for p in map_permissions(roles)),
                "signed permission union",
            )
            check(
                Permission.SYSTEM_ADMIN not in map_permissions(roles),
                "no administrative grant",
            )
        print("PASS: real reader 403, cataloger 200, reviewer/publisher union without admin")
        asyncio.run(self.rotation(access))


def main() -> int:
    if os.environ.get("TEST_KEYCLOAK_ISOLATED") != "1":
        print("BLOCKED: set TEST_KEYCLOAK_ISOLATED=1 only for an isolated disposable deployment")
        return 2
    if not all(
        os.environ.get(name) for name in ("KEYCLOAK_ADMIN_USERNAME", "KEYCLOAK_ADMIN_PASSWORD")
    ):
        print("BLOCKED: temporary administrator environment is required")
        return 2
    # Suppress third-party loggers that might print URLs containing authorization codes.
    import logging

    logging.disable(logging.CRITICAL)
    probe: KeycloakProbe | None = None
    try:
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            probe = KeycloakProbe(
                client, os.environ.get("TEST_KEYCLOAK_API_URL", "http://localhost:8000")
            )
            try:
                probe.run()
            finally:
                probe.cleanup()
        return 0
    except ProbeFailure as exc:
        print("FAIL: " + str(exc))
    except Exception as exc:
        # Do not emit traceback, response bodies, URLs or locals containing secrets.
        print("FAIL: transport/provider or configuration error (" + type(exc).__name__ + ")")
    return 1


if __name__ == "__main__":
    sys.exit(main())
