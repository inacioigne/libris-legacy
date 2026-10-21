"""Operation/resource authorization at the application boundary, without HTTP."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from itertools import combinations
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from libris.modules.bibliographic.application.history import HistoryService
from libris.modules.bibliographic.application.service import BibliographicService
from libris.modules.bibliographic.domain.audit import ActorType, ExecutionActor
from libris.modules.bibliographic.domain.editorial import (
    EditorialRecord,
    EditorialState,
)
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    Revision,
)
from libris.modules.bibliographic.infrastructure.rdf import BF, serialize_document
from libris.modules.bibliographic.infrastructure.validation import PROFILE_ID
from libris.modules.identity.application.authorization import (
    AuthorizationGuard,
    HumanContext,
    TechnicalContext,
    execution_context,
)
from libris.modules.identity.application.service import authorize
from libris.modules.identity.domain.authorization import (
    POLICIES,
    Operation,
    ResourceContext,
    ResourceKind,
)
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.domain.permissions import Permission as P
from libris.modules.identity.infrastructure.role_mapper import (
    ROLE_PERMISSIONS,
    map_permissions,
)
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF

# Independently approved matrix: adding a Permission must update this evidence explicitly.
EXPECTED = {
    "libris-reader": {"bibliographic:read", "authority:read"},
    "libris-cataloger": {
        "bibliographic:read",
        "bibliographic:create",
        "bibliographic:update",
        "authority:read",
        "metadata_revision:read",
        "metadata_revision:create",
        "editorial:submit",
    },
    "libris-authority-manager": {"bibliographic:read", "authority:read", "authority:manage"},
    "libris-reviewer": {
        "bibliographic:read",
        "authority:read",
        "metadata_revision:read",
        "metadata_audit:read",
        "editorial:approve",
        "editorial:return",
    },
    "libris-publisher": {
        "bibliographic:read",
        "authority:read",
        "metadata_revision:read",
        "metadata_audit:read",
        "editorial:publish",
        "editorial:withdraw",
        "editorial:republish",
        "editorial:archive",
    },
    "libris-admin": {
        "bibliographic:read",
        "bibliographic:create",
        "bibliographic:update",
        "bibliographic:delete",
        "authority:read",
        "authority:manage",
        "metadata_audit:read",
        "metadata_revision:read",
        "metadata_revision:create",
        "system:admin",
        "editorial:submit",
        "editorial:approve",
        "editorial:return",
        "editorial:publish",
        "editorial:withdraw",
        "editorial:republish",
        "editorial:archive",
    },
}


def human(*roles: str, permissions: frozenset[P] | None = None) -> HumanContext:
    issuer, subject = "https://issuer.example.invalid", "authorization-fixture"
    return HumanContext(
        AuthenticatedActor(
            actor_identifier(issuer, subject),
            issuer,
            subject,
            frozenset(roles),
            map_permissions(frozenset(roles)) if permissions is None else permissions,
        )
    )


def technical(*permissions: P, operations: tuple[Operation, ...]) -> TechnicalContext:
    return TechnicalContext(
        "service:fixture-import",
        frozenset(permissions),
        frozenset(operations),
        "pytest:composition-root",
    )


@pytest.mark.parametrize("role", EXPECTED)
@pytest.mark.parametrize("permission", list(P))
def test_six_roles_seventeen_permissions(role: str, permission: P) -> None:
    actor = human(role).actor
    if permission.value in EXPECTED[role]:
        authorize(actor, (permission,))
    else:
        with pytest.raises(PermissionDenied):
            authorize(actor, (permission,))


@pytest.mark.parametrize(
    "roles",
    [(), ("unknown",), *combinations(EXPECTED, 2), tuple(EXPECTED), ("unknown", "libris-reader")],
)
def test_role_unions_have_no_fallback(roles: tuple[str, ...]) -> None:
    expected = set().union(*(EXPECTED.get(role, set()) for role in roles))
    assert {p.value for p in map_permissions(frozenset(roles))} == expected


def test_permission_and_policy_inventory_is_explicit() -> None:
    assert set(ROLE_PERMISSIONS) == set(EXPECTED)
    assert {p.value for p in P} == EXPECTED["libris-admin"]
    assert set(POLICIES) == set(Operation)
    for policy in POLICIES.values():
        assert policy.resources
        assert policy.public or policy.permissions
        assert policy.permissions <= frozenset(P)


@pytest.mark.parametrize("operation", list(Operation))
def test_every_registered_operation_allows_admin_human_with_correct_resource(
    operation: Operation,
) -> None:
    policy = POLICIES[operation]
    resource = ResourceContext(next(iter(policy.resources)), "published", 1, 1)
    AuthorizationGuard().require(
        context=human("libris-admin"), operation=operation, resource=resource
    )


@pytest.mark.parametrize("operation", [op for op in Operation if not POLICIES[op].public])
def test_every_protected_operation_denies_missing_context(operation: Operation) -> None:
    with pytest.raises(PermissionDenied):
        AuthorizationGuard().require(
            context=None,
            operation=operation,
            resource=ResourceContext(next(iter(POLICIES[operation].resources))),
        )


@pytest.mark.parametrize(
    "operation",
    [op for op in Operation if not POLICIES[op].public and op != Operation.SYSTEM_ADMIN],
)
def test_system_admin_alone_has_no_functional_bypass(operation: Operation) -> None:
    with pytest.raises(PermissionDenied):
        AuthorizationGuard().require(
            context=human(permissions=frozenset({P.SYSTEM_ADMIN})),
            operation=operation,
            resource=ResourceContext(next(iter(POLICIES[operation].resources))),
        )


@pytest.mark.parametrize("operation", ["unknown", "bibliographic.create", None])
def test_unregistered_or_untyped_operation_is_denied(operation: object) -> None:
    with pytest.raises(PermissionDenied):
        AuthorizationGuard().require(
            context=human("libris-admin"),
            operation=operation,
            resource=ResourceContext(ResourceKind.BIBLIOGRAPHIC),
        )  # type: ignore[arg-type]


def test_unknown_permission_and_wrong_resource_are_denied() -> None:
    with pytest.raises(PermissionDenied):
        authorize(human("libris-admin").actor, ("unknown",))  # type: ignore[arg-type]
    with pytest.raises(PermissionDenied):
        AuthorizationGuard().require(
            context=human("libris-admin"),
            operation=Operation.AUTHORITY_MANAGE,
            resource=ResourceContext(ResourceKind.BIBLIOGRAPHIC),
        )


@pytest.mark.parametrize(
    "operation,grants",
    [
        (Operation.METADATA_REVISION_RESTORE, {P.METADATA_REVISION_READ}),
        (Operation.METADATA_REVISION_RESTORE, {P.METADATA_REVISION_CREATE}),
        (Operation.METADATA_EDITORIAL_HISTORY, {P.METADATA_REVISION_READ}),
        (Operation.METADATA_EDITORIAL_HISTORY, {P.METADATA_AUDIT_READ}),
        (Operation.BIBLIOGRAPHIC_READ_EDITORIAL, {P.BIBLIOGRAPHIC_READ}),
        (Operation.BIBLIOGRAPHIC_READ_EDITORIAL, {P.METADATA_REVISION_READ}),
    ],
)
def test_composed_permissions_require_all(operation: Operation, grants: set[P]) -> None:
    with pytest.raises(PermissionDenied):
        AuthorizationGuard().require(
            context=human(permissions=frozenset(grants)),
            operation=operation,
            resource=ResourceContext(next(iter(POLICIES[operation].resources))),
        )


@pytest.mark.parametrize(
    "kind,operation",
    [
        (ResourceKind.BIBLIOGRAPHIC, Operation.BIBLIOGRAPHIC_READ_PUBLIC),
        (ResourceKind.AUTHORITY, Operation.AUTHORITY_READ_PUBLIC),
    ],
)
@pytest.mark.parametrize(
    "state,public,selected",
    [
        ("draft", None, None),
        ("in_review", None, None),
        ("approved", None, None),
        ("withdrawn", 1, 1),
        ("archived", 1, 1),
        ("published", None, None),
        ("published", 1, 2),
        ("published", True, True),
        ("unknown", 1, 1),
    ],
)
def test_public_requires_exact_published_projection(
    kind: ResourceKind, operation: Operation, state: str, public: int | None, selected: int | None
) -> None:
    with pytest.raises(PermissionDenied):
        AuthorizationGuard().require(
            context=None,
            operation=operation,
            resource=ResourceContext(kind, state, public, selected),
        )


def test_technical_scope_and_grants_are_independent() -> None:
    guard = AuthorizationGuard()
    context = technical(P.BIBLIOGRAPHIC_CREATE, operations=(Operation.BIBLIOGRAPHIC_CREATE,))
    guard.require(
        context=context,
        operation=Operation.BIBLIOGRAPHIC_CREATE,
        resource=ResourceContext(ResourceKind.BIBLIOGRAPHIC),
    )
    for limited in (
        replace(context, permissions=frozenset()),
        replace(context, operations=frozenset()),
    ):
        with pytest.raises(PermissionDenied):
            guard.require(
                context=limited,
                operation=Operation.BIBLIOGRAPHIC_CREATE,
                resource=ResourceContext(ResourceKind.BIBLIOGRAPHIC),
            )
    with pytest.raises(PermissionDenied):
        HumanContext(context)  # type: ignore[arg-type]
    with pytest.raises(PermissionDenied):
        execution_context(context, human("libris-admin").actor)


@pytest.mark.parametrize(
    "operation", [op for op in Operation if not POLICIES[op].technical and not POLICIES[op].public]
)
def test_technical_cannot_claim_human_operation(operation: Operation) -> None:
    with pytest.raises(PermissionDenied):
        technical(*POLICIES[operation].permissions, operations=(operation,))
    # Even an in-process constructed context with functional permissions cannot use human policy.
    context = (
        technical(*POLICIES[operation].permissions, operations=())
        if operation != Operation.SYSTEM_ADMIN
        else technical(operations=())
    )
    with pytest.raises(PermissionDenied):
        AuthorizationGuard().require(
            context=context,
            operation=operation,
            resource=ResourceContext(next(iter(POLICIES[operation].resources))),
        )


def service_double() -> tuple[BibliographicService, MagicMock]:
    sessions = MagicMock()
    session = MagicMock()
    session.execute = AsyncMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    session.begin.return_value.__aenter__ = AsyncMock()
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    service = BibliographicService(sessions)
    for store, methods in (
        (service.store, ("create", "save", "get", "advance")),
        (service.audit, ("append",)),
        (service.editorial, ("lock_resource", "initialize", "save", "get")),
    ):
        for method in methods:
            setattr(store, method, AsyncMock())
    return service, sessions


def document() -> tuple[ResourceIdentity, Graph]:
    identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
    graph = Graph()
    title = BNode()
    graph.add((URIRef(identity.uri), RDF.type, BF.Work))
    graph.add((URIRef(identity.uri), BF.title, title))
    graph.add((title, RDF.type, BF.Title))
    graph.add((title, BF.mainTitle, Literal("Authorization fixture")))
    return identity, graph


@pytest.mark.parametrize("operation", ["create", "revise", "read", "restore"])
@pytest.mark.parametrize(
    "context",
    [
        None,
        human("libris-reader"),
        technical(operations=(Operation.BIBLIOGRAPHIC_CREATE,)),
        {"roles": ["libris-admin"]},
    ],
)
def test_direct_application_denies_before_storage(operation: str, context: object) -> None:
    service, sessions = service_double()
    identity, graph = document()
    calls = {
        "create": lambda: service.create(
            identity, graph, Provenance("fixture", "test"), context=context
        ),
        "revise": lambda: service.revise(
            identity.internal_id, 1, graph, Provenance("fixture", "test"), context=context
        ),
        "read": lambda: service.read(identity.internal_id, context=context),
        "restore": lambda: service.restore_content(
            identity.internal_id, 1, 1, Provenance("fixture", "test"), context=context
        ),
    }
    with pytest.raises(PermissionDenied):
        asyncio.run(calls[operation]())
    sessions.assert_not_called()
    service.store.save.assert_not_awaited()
    service.audit.append.assert_not_awaited()


@pytest.mark.parametrize(
    "context",
    [
        human("libris-cataloger"),
        technical(P.BIBLIOGRAPHIC_CREATE, operations=(Operation.BIBLIOGRAPHIC_CREATE,)),
    ],
)
def test_direct_create_authorized_with_real_validation_and_existing_audit(
    context: HumanContext | TechnicalContext,
) -> None:
    service, _ = service_double()
    identity, graph = document()
    revision = asyncio.run(
        service.create(identity, graph, Provenance("fixture", "test"), context=context)
    )
    assert revision.actor_id == (
        context.actor.actor_id if isinstance(context, HumanContext) else context.actor_id
    )
    service.store.create.assert_awaited_once()
    service.store.save.assert_awaited_once()
    service.audit.append.assert_awaited_once()
    event = service.audit.append.await_args.args[1]
    assert event.actor_type == (
        ActorType.HUMAN if isinstance(context, HumanContext) else ActorType.SERVICE
    )


def test_execution_actor_is_not_authorization() -> None:
    service, sessions = service_double()
    identity, graph = document()
    with pytest.raises(PermissionDenied):
        asyncio.run(
            service.create(
                identity,
                graph,
                Provenance("fixture", "test"),
                execution_actor=ExecutionActor("service:old-import", ActorType.SERVICE),
            )
        )
    sessions.assert_not_called()


@pytest.mark.parametrize("state", [None, *EditorialState])
def test_read_public_uses_selected_revision_never_current(state: EditorialState | None) -> None:
    import json

    service, _ = service_double()
    identity, graph = document()
    published = state in {
        EditorialState.PUBLISHED,
        EditorialState.WITHDRAWN,
        EditorialState.ARCHIVED,
    }
    record = (
        None
        if state is None
        else EditorialRecord(
            identity,
            current_revision=2,
            editorial_state=state,
            approved_revision=2 if state == EditorialState.APPROVED else None,
            published_revision=1 if published else None,
            candidate_state=EditorialState.DRAFT
            if state in {EditorialState.PUBLISHED, EditorialState.WITHDRAWN}
            else None,
        )
    )
    service.editorial.get.return_value = record
    legacy = Revision(identity, 1, datetime.now(UTC), Provenance("legacy", "import"), PROFILE_ID)
    service.store.get.return_value = (legacy, json.loads(serialize_document(graph, "json-ld")))
    result = asyncio.run(service.read_public(identity.internal_id))
    if state == EditorialState.PUBLISHED:
        assert result is not None and result[0].number == 1 and result[0].actor_id is None
        assert service.store.get.await_args.args[2] == 1
    else:
        assert result is None
        service.store.get.assert_not_awaited()


@pytest.mark.parametrize(
    "method",
    [
        "read_revision",
        "list_revisions",
        "list_events",
        "read_event",
        "read_editorial",
        "compare_revisions",
    ],
)
def test_history_missing_context_has_no_storage_access(method: str) -> None:
    sessions = MagicMock()
    history = HistoryService(sessions)
    identifier = uuid4()
    calls = {
        "read_revision": lambda: history.read_revision(identifier),
        "list_revisions": lambda: history.list_revisions(identifier),
        "list_events": lambda: history.list_events(identifier),
        "read_event": lambda: history.read_event(identifier, uuid4()),
        "read_editorial": lambda: history.read_editorial(identifier),
        "compare_revisions": lambda: history.compare_revisions(identifier, 1, 2),
    }
    with pytest.raises(PermissionDenied):
        asyncio.run(calls[method]())
    sessions.assert_not_called()


def test_authority_maintenance_has_its_own_grant() -> None:
    from libris.modules.bibliographic.infrastructure.validation import (
        AUTHORITY_PROFILE_ID,
    )
    from rdflib.namespace import RDFS

    service, sessions = service_double()
    identity = ResourceIdentity.create("https://catalog.example.invalid/authorities")
    graph = Graph()
    graph.add((URIRef(identity.uri), RDF.type, BF.Person))
    graph.add((URIRef(identity.uri), RDFS.label, Literal("Synthetic authority")))
    with pytest.raises(PermissionDenied):
        asyncio.run(
            service.create(
                identity,
                graph,
                Provenance("fixture", "test"),
                profile=AUTHORITY_PROFILE_ID,
                context=human("libris-cataloger"),
            )
        )
    sessions.assert_not_called()
    result = asyncio.run(
        service.create(
            identity,
            graph,
            Provenance("fixture", "test"),
            profile=AUTHORITY_PROFILE_ID,
            context=human("libris-authority-manager"),
        )
    )
    assert result.profile == AUTHORITY_PROFILE_ID
    guard = AuthorizationGuard()
    with pytest.raises(PermissionDenied):
        guard.require(
            context=human("libris-authority-manager"),
            operation=Operation.BIBLIOGRAPHIC_UPDATE,
            resource=ResourceContext(ResourceKind.BIBLIOGRAPHIC),
        )


def test_public_guard_is_anonymous_and_guard_is_injectable() -> None:
    for kind, operation in (
        (ResourceKind.BIBLIOGRAPHIC, Operation.BIBLIOGRAPHIC_READ_PUBLIC),
        (ResourceKind.AUTHORITY, Operation.AUTHORITY_READ_PUBLIC),
    ):
        AuthorizationGuard().require(
            context=None, operation=operation, resource=ResourceContext(kind, "published", 1, 1)
        )
    guard = MagicMock(spec=AuthorizationGuard)
    guard.require.side_effect = PermissionDenied()
    sessions = MagicMock()
    service = BibliographicService(sessions, authorization=guard)
    identity, graph = document()
    with pytest.raises(PermissionDenied):
        asyncio.run(
            service.create(
                identity, graph, Provenance("fixture", "test"), context=human("libris-admin")
            )
        )
    guard.require.assert_called_once()
    sessions.assert_not_called()
