"""Observable records, privacy, concurrency and preserved M03 boundaries."""

import asyncio
import io
import json
import logging
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from libris.core.correlation import request_id_context
from libris.main import create_app
from libris.modules.bibliographic.domain.models import Provenance
from libris.modules.identity.api.dependencies import (
    get_identity_service,
    require_permissions,
)
from libris.modules.identity.application.authorization import AuthorizationGuard
from libris.modules.identity.application.service import IdentityService
from libris.modules.identity.domain.authorization import Operation as Op
from libris.modules.identity.domain.authorization import ResourceContext, ResourceKind
from libris.modules.identity.domain.exceptions import (
    AuthenticationError,
    PermissionDenied,
)
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.domain.permissions import Permission as P
from libris.modules.security_audit.domain.events import (
    Action,
    ActorType,
    EventType,
    Outcome,
    Reason,
    SecurityEvent,
)
from libris.modules.security_audit.infrastructure.logging import LoggingSecuritySink
from pydantic import ValidationError
from test_authorization import document, human, service_double, technical
from test_identity import Provider


class Capture:
    def __init__(self) -> None:
        self.events: list[SecurityEvent] = []

    def emit(self, event: SecurityEvent) -> None:
        self.events.append(event)


class FailingSink:
    def emit(self, event: SecurityEvent) -> None:
        raise OSError("SECRET-SINK-EXCEPTION")


@pytest.fixture
def provider() -> Iterator[Provider]:
    value = Provider()
    yield value
    asyncio.run(value.client.aclose())


def envelope(**changes: object) -> SecurityEvent:
    return SecurityEvent.model_validate(
        {
            "event_type": "authentication.failed",
            "correlation_id": uuid4(),
            "action": "identity.authenticate",
            "outcome": "denied",
            "component": "api",
            **changes,
        }
    )


def test_envelope_utc_deterministic_and_effective_logging() -> None:
    event = envelope(occurred_at=datetime(2026, 10, 14, tzinfo=timezone(timedelta(hours=-4))))
    assert event.occurred_at == datetime(2026, 10, 14, 4, tzinfo=UTC)
    assert event.actor_id is None and event.actor_type == ActorType.UNKNOWN
    assert event.serialize() == event.serialize()
    with pytest.raises(ValidationError):
        event.outcome = Outcome.SUCCESS
    sink = LoggingSecuritySink()
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    sink.logger.addHandler(handler)
    try:
        sink.emit(event)
        assert json.loads(output.getvalue()) == json.loads(event.serialize())
        assert json.loads(output.getvalue())["occurred_at"].endswith("Z")
    finally:
        sink.logger.removeHandler(handler)


@pytest.mark.parametrize(
    "changes",
    [
        {"occurred_at": datetime(2026, 10, 14)},
        {"correlation_id": "x\r\nInjected"},
        {"correlation_id": "x" * 10000},
        {"event_id": "bad"},
        {"actor_id": "private@example.invalid"},
        {"actor_type": "human"},
        {"actor_id": actor_identifier("issuer", "subject")},
        {"resource_id": "secret"},
        {"event_type": "arbitrary"},
        {"outcome": "arbitrary"},
        {"action": "arbitrary"},
        {"reason": "SECRET-EXCEPTION"},
        {"component": "unknown"},
    ],
)
def test_invalid_contract(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        envelope(**changes)


@pytest.mark.parametrize(
    "field",
    [
        "Authorization",
        "Cookie",
        "Set-Cookie",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "password",
        "code",
        "code_verifier",
        "csrf",
        "session_id",
        "request_body",
        "response_body",
        "query_string",
        "claims",
    ],
)
def test_sensitive_extras_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        envelope(**{field: "SENSITIVE-SENTINEL"})


@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "missing",
        "malformed",
        "invalid",
        "signature",
        "expired",
        "issuer",
        "audience",
        "id_token",
        "client",
        "jwks",
    ],
)
def test_authentication_records_and_privacy(provider: Provider, mode: str) -> None:
    capture = Capture()
    service = IdentityService(provider.validator, "client", "libris-api", observer=capture)
    token = provider.token({"sub": "SUBJECT-SENTINEL", "email": "EMAIL-SENTINEL"})
    changes = {
        "expired": {"exp": 1},
        "issuer": {"iss": "wrong"},
        "audience": {"aud": "wrong"},
        "id_token": {"typ": "ID"},
        "client": {"azp": "other"},
    }.get(mode)
    if changes:
        token = provider.token(changes)
    if mode == "signature":
        token = provider.token(private=provider.other)
    if mode == "jwks":
        provider.status = 503
    if mode == "invalid":
        token = "ACCESS-TOKEN-SENTINEL"
    headers = {"Authorization": "Bearer " + token, "Cookie": "COOKIE-SENTINEL"}
    if mode == "missing":
        headers.pop("Authorization")
    if mode == "malformed":
        headers["Authorization"] = "Basic PASSWORD-SENTINEL"
    app = create_app(provider.settings)
    app.dependency_overrides[get_identity_service] = lambda: service
    with TestClient(app) as client:
        response = client.get("/api/v1/identity/me", headers=headers)
        assert response.status_code == (200 if mode == "valid" else 401)
        if mode != "valid":
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.json()["contract_version"] == "1.0"
        assert client.get("/health").status_code == 200
    assert len(capture.events) == 1
    event = capture.events[0]
    assert str(event.correlation_id) == response.headers["x-request-id"]
    assert event.action == Action.AUTHENTICATE
    assert event.actor_id == (response.json()["actor_id"] if mode == "valid" else None)
    expected = (
        Outcome.SUCCESS
        if mode == "valid"
        else Outcome.UNAVAILABLE
        if mode == "jwks"
        else Outcome.DENIED
    )
    assert event.outcome == expected
    if mode == "client":
        assert event.reason == Reason.CLIENT_NOT_ALLOWED
    for secret in (
        token,
        "SUBJECT-SENTINEL",
        "EMAIL-SENTINEL",
        "COOKIE-SENTINEL",
        "PASSWORD-SENTINEL",
    ):
        assert secret not in event.serialize()


def test_provider_backoff_keeps_unavailable_category(provider: Provider) -> None:
    capture = Capture()
    provider.status = 503
    service = IdentityService(provider.validator, "client", "libris-api", observer=capture)

    async def scenario() -> None:
        for _ in range(2):
            with pytest.raises(AuthenticationError):
                await service.authenticate(provider.token())

    asyncio.run(scenario())
    assert all(e.outcome == Outcome.UNAVAILABLE and e.actor_id is None for e in capture.events)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("allowed", [True, False])
def test_permission_dependency_preserves_http(provider: Provider, allowed: bool) -> None:
    capture = Capture()
    app = create_app(provider.settings)
    service = IdentityService(provider.validator, "client", "libris-api", observer=capture)
    app.dependency_overrides[get_identity_service] = lambda: service

    @app.get("/probe")
    def probe(
        actor: Annotated[AuthenticatedActor, Depends(require_permissions(P.SYSTEM_ADMIN))],
    ) -> str:
        return actor.actor_id

    roles = ["libris-admin"] if allowed else ["libris-reader"]
    token = provider.token({"resource_access": {"libris-api": {"roles": roles}}})
    with TestClient(app) as client:
        response = client.get("/probe", headers={"Authorization": "Bearer " + token})
    assert response.status_code == (200 if allowed else 403)
    assert len(capture.events) == 2
    event = capture.events[-1]
    assert event.actor_id == actor_identifier(provider.settings.oidc_issuer_url, "subject")
    assert event.outcome == (Outcome.SUCCESS if allowed else Outcome.DENIED)
    assert event.required_permissions == (P.SYSTEM_ADMIN,)


@pytest.mark.parametrize(
    "case,reason",
    [
        ("allowed", Reason.NONE),
        ("missing", Reason.MISSING_CONTEXT),
        ("permission", Reason.INSUFFICIENT_PERMISSIONS),
        ("unknown", Reason.INVALID_OPERATION_RESOURCE),
        ("resource", Reason.INVALID_OPERATION_RESOURCE),
        ("technical", Reason.TECHNICAL_SCOPE),
        ("admin", Reason.INSUFFICIENT_PERMISSIONS),
    ],
)
def test_guard_decisions(case: str, reason: Reason) -> None:
    capture = Capture()
    guard = AuthorizationGuard(capture)
    context = human("libris-cataloger")
    operation = Op.BIBLIOGRAPHIC_CREATE
    resource_id = uuid4()
    resource = ResourceContext(ResourceKind.BIBLIOGRAPHIC, resource_id=resource_id)
    if case == "missing":
        context = None
    elif case == "permission":
        context = human("libris-reader")
    elif case == "unknown":
        operation = "SECRET-UNKNOWN-OPERATION"  # type: ignore[assignment]
    elif case == "resource":
        resource = ResourceContext(ResourceKind.SYSTEM)
    elif case == "technical":
        context = technical(P.BIBLIOGRAPHIC_CREATE, operations=(Op.BIBLIOGRAPHIC_UPDATE,))
    elif case == "admin":
        context = human(permissions=frozenset({P.SYSTEM_ADMIN}))
    if case == "allowed":
        guard.require(context=context, operation=operation, resource=resource)
    else:
        with pytest.raises(PermissionDenied):
            guard.require(context=context, operation=operation, resource=resource)
    event = capture.events[0]
    assert event.reason == reason
    assert event.outcome == (Outcome.SUCCESS if case == "allowed" else Outcome.DENIED)
    assert (event.actor_id is None) == (case == "missing")
    if case != "resource":
        assert event.resource_id == resource_id
    assert "SECRET-UNKNOWN-OPERATION" not in event.serialize()


@pytest.mark.parametrize("allowed", [True, False])
def test_sink_failure_preserves_decision(allowed: bool, caplog: pytest.LogCaptureFixture) -> None:
    guard = AuthorizationGuard(FailingSink())
    context = human("libris-admin" if allowed else "libris-reader")
    if allowed:
        guard.require(
            context=context,
            operation=Op.BIBLIOGRAPHIC_CREATE,
            resource=ResourceContext(ResourceKind.BIBLIOGRAPHIC),
        )
    else:
        with pytest.raises(PermissionDenied):
            guard.require(
                context=context,
                operation=Op.BIBLIOGRAPHIC_CREATE,
                resource=ResourceContext(ResourceKind.BIBLIOGRAPHIC),
            )
    assert "security_audit_sink_unavailable" in caplog.text
    assert "SECRET-SINK-EXCEPTION" not in caplog.text


@pytest.mark.parametrize("header", ["bad", "x" * 10000, "a\r\nb", "0" * 32, str(uuid4()).upper()])
def test_invalid_http_correlation(header: str) -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/identity/me", headers={"X-Request-ID": header})
    value = response.headers["x-request-id"]
    assert len(value) == 36 and str(UUID(value)) == value and value != header


def test_concurrent_http_correlation() -> None:
    capture = Capture()

    class Validator:
        async def validate(self, token: str) -> Mapping[str, object]:
            await asyncio.sleep(0)
            return {"iss": "issuer", "sub": token, "azp": "libris-web"}

    app = create_app()
    service = IdentityService(Validator(), "client", "libris-api", observer=capture)
    app.dependency_overrides[get_identity_service] = lambda: service
    identifiers = [str(uuid4()) for _ in range(12)]

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.get(
                        "/api/v1/identity/me",
                        headers={
                            "X-Request-ID": identifier,
                            "Authorization": "Bearer subject-" + identifier,
                        },
                    )
                    for identifier in identifiers
                )
            )
        for identifier, response in zip(identifiers, responses, strict=True):
            assert response.headers["x-request-id"] == identifier
        assert request_id_context.get() is None

    asyncio.run(scenario())
    assert {str(e.correlation_id) for e in capture.events} == set(identifiers)
    for event in capture.events:
        assert event.actor_id == actor_identifier("issuer", "subject-" + str(event.correlation_id))


def test_security_does_not_fabricate_catalog_history() -> None:
    service, sessions = service_double()
    capture = Capture()
    service.authorization = AuthorizationGuard(capture)
    identity, graph = document()
    with pytest.raises(PermissionDenied):
        asyncio.run(
            service.create(
                identity, graph, Provenance("test", "test"), context=human("libris-reader")
            )
        )
    sessions.assert_not_called()
    service.audit.append.assert_not_awaited()
    assert capture.events[0].resource_id == identity.internal_id
    revision = asyncio.run(
        service.create(
            identity, graph, Provenance("test", "test"), context=human("libris-cataloger")
        )
    )
    service.audit.append.assert_awaited_once()
    m03_event = service.audit.append.await_args.args[1]
    assert m03_event.revision_number == revision.number == 1
    assert m03_event.event_type.value == "record.created"
    assert all(
        e.event_type in (EventType.AUTHORIZATION_GRANTED, EventType.AUTHORIZATION_DENIED)
        for e in capture.events
    )


def test_authentication_actual_logs_exclude_sentinels(provider: Provider) -> None:
    service = IdentityService(provider.validator, "client", "libris-api")
    output = io.StringIO()
    logger = logging.getLogger("libris.security_audit.events")
    handler = logging.StreamHandler(output)
    logger.addHandler(handler)
    token = provider.token({"sub": "SUBJECT-SENTINEL", "email": "EMAIL-SENTINEL"})
    try:
        asyncio.run(service.authenticate(token))
        with pytest.raises(AuthenticationError):
            asyncio.run(service.authenticate("ACCESS-TOKEN-SENTINEL"))
    finally:
        logger.removeHandler(handler)
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert records[0]["actor_id"] == actor_identifier(
        provider.settings.oidc_issuer_url, "SUBJECT-SENTINEL"
    )
    assert records[1]["actor_id"] is None
    for secret in (token, "SUBJECT-SENTINEL", "EMAIL-SENTINEL", "ACCESS-TOKEN-SENTINEL"):
        assert secret not in output.getvalue()


def test_preflight_and_500_have_safe_correlation() -> None:
    app = create_app()

    @app.get("/broken")
    def broken() -> None:
        raise RuntimeError("SECRET-EXCEPTION")

    with TestClient(app, raise_server_exceptions=False) as client:
        for response in (
            client.options(
                "/health",
                headers={"Origin": "https://wrong", "Access-Control-Request-Method": "GET"},
            ),
            client.get("/broken"),
            client.get(
                "/health", headers=[("X-Request-ID", str(uuid4())), ("X-Request-ID", str(uuid4()))]
            ),
        ):
            assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
            assert "SECRET-EXCEPTION" not in response.text
    assert request_id_context.get() is None


@pytest.mark.parametrize("allowed", [True, False])
def test_authentication_sink_failure_preserves_result(provider: Provider, allowed: bool) -> None:
    service = IdentityService(provider.validator, "client", "libris-api", observer=FailingSink())
    if allowed:
        actor = asyncio.run(service.authenticate(provider.token()))
        assert actor.actor_id == actor_identifier(provider.settings.oidc_issuer_url, "subject")
    else:
        with pytest.raises(AuthenticationError):
            asyncio.run(service.authenticate("invalid"))
