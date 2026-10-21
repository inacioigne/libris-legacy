"""Real governance probe, exclusively for a newly created disposable Keycloak.

Reuse the PKCE harness; never print responses, claims, credentials or tracebacks.
"""

import logging
import os
import sys
import time

import httpx
from fastapi import Depends
from fastapi.testclient import TestClient
from keycloak_harness import (
    ISSUER,
    KeycloakProbe,
    ProbeFailure,
    check,
    document,
    string,
)
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.identity.api.dependencies import require_permissions
from libris.modules.identity.domain.models import AuthenticatedActor
from libris.modules.identity.domain.permissions import Permission


def run(probe: KeycloakProbe) -> None:
    probe.provision()
    probe.assign(("libris-cataloger",))

    def issue() -> dict[str, object]:
        code, verifier = probe.code()
        return document(probe.exchange(code, verifier), "new PKCE issuance")

    initial = issue()
    access = string(initial.get("access_token"), "initial access token")
    settings = Settings(
        _env_file=None, oidc_enabled=True, oidc_issuer_url=ISSUER, oidc_clock_skew_seconds=0
    )
    app = create_app(settings)

    @app.get("/probe/create")
    def create(
        actor: AuthenticatedActor = Depends(require_permissions(Permission.BIBLIOGRAPHIC_CREATE)),
    ) -> str:
        return actor.actor_id

    with TestClient(app) as api:
        original = api.get("/api/v1/identity/me", headers={"Authorization": "Bearer " + access})
        check(original.status_code == 200, "real token accepted by /me")
        actor_id = original.json()["actor_id"]
        check(
            api.get("/probe/create", headers={"Authorization": "Bearer " + access}).status_code
            == 200,
            "cataloger authorized",
        )
        clients = probe.client.get(
            probe.admin_url + "/clients",
            params={"clientId": "libris-api"},
            headers=probe.admin_headers,
        ).json()
        client_id = string(clients[0].get("id"), "API client ID")
        role = document(
            probe.client.get(
                probe.admin_url + f"/clients/{client_id}/roles/libris-cataloger",
                headers=probe.admin_headers,
            ),
            "cataloger role",
        )
        response = probe.client.request(
            "DELETE",
            probe.admin_url + f"/users/{probe.user_id}/role-mappings/clients/{client_id}",
            headers=probe.admin_headers,
            json=[role],
        )
        check(response.status_code == 204, "role revoked")
        after = issue()
        new_access = string(after.get("access_token"), "new access token")
        new_headers = {"Authorization": "Bearer " + new_access}
        me = api.get("/api/v1/identity/me", headers=new_headers)
        check(me.status_code == 200 and me.json()["actor_id"] == actor_id, "stable identity")
        check(me.json()["permissions"] == [], "no permission after role revocation")
        check(api.get("/probe/create", headers=new_headers).status_code == 403, "new token denied")
        check(
            api.get("/probe/create", headers={"Authorization": "Bearer " + access}).status_code
            == 200,
            "old signed token retains permission",
        )
        print("PASS: real PKCE grant/revoke; stable /me; old token 200; new token 403")
        response = probe.client.post(
            probe.admin_url + f"/users/{probe.user_id}/logout", headers=probe.admin_headers
        )
        check(response.status_code == 204, "provider sessions revoked")
        refresh = probe.client.post(
            probe.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": "libris-web",
                "refresh_token": string(after.get("refresh_token"), "refresh token"),
            },
        )
        check(refresh.status_code == 400, "refresh denied after session revocation")
        print("PASS: provider session logout prevents refresh; existing JWT still offline")
        active = issue()
        response = probe.client.put(
            probe.admin_url + f"/users/{probe.user_id}",
            headers=probe.admin_headers,
            json={"enabled": False},
        )
        check(response.status_code == 204, "disable owned test account")
        disabled_refresh = probe.client.post(
            probe.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": "libris-web",
                "refresh_token": string(active.get("refresh_token"), "active refresh token"),
            },
        )
        check(disabled_refresh.status_code == 400, "disabled account cannot refresh")
        # Reactivate only this execution's account and shorten lifetime only on its
        # dedicated realm. No persisted deployment may be used by this harness.
        check(
            probe.client.put(
                probe.admin_url + f"/users/{probe.user_id}",
                headers=probe.admin_headers,
                json={"enabled": True},
            ).status_code == 204,
            "reactivate owned account",
        )
        check(
            probe.client.put(
                probe.admin_url,
                headers=probe.admin_headers,
                json={"accessTokenLifespan": 2},
            ).status_code == 204,
            "short expiration in disposable realm",
        )
        short = string(issue().get("access_token"), "short token")
        time.sleep(3)
        expired = api.get("/api/v1/identity/me", headers={"Authorization": "Bearer " + short})
        check(expired.status_code == 401, "expired real token rejected")
        check(expired.headers.get("www-authenticate") == "Bearer", "Bearer challenge")
        print("PASS: disable/reactivate and expired real token 401 Bearer")
    events = probe.client.get(probe.admin_url + "/admin-events", headers=probe.admin_headers)
    check(events.status_code == 200, "admin event query")
    entries = events.json()
    owned = [event for event in entries if probe.user_id in event.get("resourcePath", "")]
    check({e["operationType"] for e in owned} >= {"CREATE", "UPDATE", "DELETE"}, "admin trail")
    check(all(not e.get("representation") for e in owned), "no sensitive representation")
    check(
        all(e.get("time") and e.get("authDetails", {}).get("userId") for e in owned),
        "attribution",
    )
    print("PASS: provider admin trail has actor/time/resource/operation; no representation")


def main() -> int:
    if (
        os.environ.get("TEST_KEYCLOAK_ISOLATED") != "1"
        or os.environ.get("TEST_KEYCLOAK_GOVERNANCE") != "1"
        or not os.environ.get("TEST_KEYCLOAK_ISSUER")
    ):
        print("BLOCKED: explicit disposable governance deployment required")
        return 2
    logging.disable(logging.CRITICAL)
    try:
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            probe = KeycloakProbe(client, "unused")
            try:
                run(probe)
            finally:
                probe.cleanup()
        return 0
    except ProbeFailure as exc:
        print("FAIL: " + str(exc))
    except Exception as exc:
        print("FAIL: provider/configuration error (" + type(exc).__name__ + ")")
    return 1


if __name__ == "__main__":
    sys.exit(main())
