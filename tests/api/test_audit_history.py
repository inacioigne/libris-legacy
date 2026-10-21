"""Domain, semantic comparison and authorization without external services."""

import asyncio
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from libris.modules.bibliographic.application.audit import (
    audit_actor,
    content_event,
    editorial_event,
)
from libris.modules.bibliographic.application.comparison import compare_graphs
from libris.modules.bibliographic.application.history import HistoryService
from libris.modules.bibliographic.application.service import BibliographicService
from libris.modules.bibliographic.domain.audit import (
    ActorType,
    AuditEvent,
    ExecutionActor,
    RestorationKind,
)
from libris.modules.bibliographic.domain.audit import (
    AuditEventType as E,
)
from libris.modules.bibliographic.domain.editorial import (
    EditorialOperation as Op,
)
from libris.modules.bibliographic.domain.editorial import (
    EditorialRecord,
    EditorialScope,
    PublicationReadiness,
    prepare_transition,
)
from libris.modules.bibliographic.domain.editorial import (
    EditorialState as S,
)
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    Revision,
)
from libris.modules.bibliographic.infrastructure.audit import validate_page
from libris.modules.bibliographic.infrastructure.profiles import load_legacy_profile
from libris.modules.bibliographic.infrastructure.rdf import (
    parse_document,
    serialize_document,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    PROFILE_ID,
)
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.domain.permissions import Permission as P
from libris.modules.identity.infrastructure.role_mapper import map_permissions
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import XSD

NOW = datetime.now(UTC)
IDENTITY = ResourceIdentity.create("https://catalog.example.invalid/resources")


def actor(role: str = "libris-admin") -> AuthenticatedActor:
    issuer, subject = "https://issuer.example.invalid", "synthetic-subject"
    roles = frozenset({role})
    return AuthenticatedActor(
        actor_identifier(issuer, subject), issuer, subject, roles, map_permissions(roles)
    )


def event(kind: E = E.CREATED) -> AuditEvent:
    if kind in {E.SUBMITTED, E.APPROVED, E.PUBLISHED, E.WITHDRAWN, E.RETURNED, E.ARCHIVED}:
        previous, new = {
            E.SUBMITTED: (S.DRAFT, S.IN_REVIEW),
            E.APPROVED: (S.IN_REVIEW, S.APPROVED),
            E.PUBLISHED: (S.APPROVED, S.PUBLISHED),
            E.WITHDRAWN: (S.PUBLISHED, S.WITHDRAWN),
            E.RETURNED: (S.IN_REVIEW, S.DRAFT),
            E.ARCHIVED: (S.WITHDRAWN, S.ARCHIVED),
        }[kind]
        return AuditEvent(
            uuid4(),
            kind,
            IDENTITY,
            1,
            NOW,
            "fixture",
            "test",
            PROFILE_ID,
            actor().actor_id,
            ActorType.HUMAN,
            "Synthetic decision",
            previous,
            new,
            scope=EditorialScope.RECORD,
            previous_version=1,
            new_version=2,
        )
    if kind == E.SUPERSEDED:
        return AuditEvent(
            uuid4(),
            kind,
            IDENTITY,
            1,
            NOW,
            "fixture",
            "test",
            PROFILE_ID,
            reason="Synthetic replacement",
            related_resource_id=uuid4(),
        )
    if kind == E.RESTORED:
        return AuditEvent(
            uuid4(),
            kind,
            IDENTITY,
            2,
            NOW,
            "fixture",
            "test",
            PROFILE_ID,
            restoration_kind=RestorationKind.CONTENT,
            source_revision=1,
        )
    return AuditEvent(
        uuid4(), kind, IDENTITY, 2 if kind == E.REVISED else 1, NOW, "fixture", "test", PROFILE_ID
    )


@pytest.mark.parametrize("kind", list(E))
def test_taxonomy_and_immutable_events(kind: E) -> None:
    observed = event(kind)
    assert observed.event_type.value.startswith("record.")
    with pytest.raises(FrozenInstanceError):
        observed.reason = "tampered"  # type: ignore[misc]
    assert len(E) == 10


@pytest.mark.parametrize(
    "changes",
    [
        {"event_type": "arbitrary"},
        {"revision_number": -1},
        {"revision_number": 0},
        {"revision_number": True},
        {"revision_number": 1.5},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"source": " "},
        {"process": ""},
        {"profile": ""},
        {"actor_id": "forged"},
        {"actor_type": "service"},
        {"actor_id": "forged", "actor_type": "human"},
        {"actor_id": actor().actor_id, "actor_type": "service"},
        {"actor_type": "anonymous"},
        {"event_type": E.SUPERSEDED},
        {"related_resource_id": uuid4()},
        {"restoration_kind": "content"},
        {"source_revision": 1},
        {"previous_state": "draft"},
        {"reason": " "},
        {"event_type": E.REVISED},
        {"revision_number": 2},
    ],
)
def test_invalid_combinations(changes: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        replace(event(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"new_state": S.PUBLISHED},
        {"scope": None},
        {"previous_version": None},
        {"new_version": 1},
        {"new_version": True},
        {"previous_version": 0},
        {"actor_id": None, "actor_type": None},
        {"previous_state": "invented"},
    ],
)
def test_invalid_editorial_combinations(changes: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        replace(event(E.APPROVED), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"restoration_kind": None},
        {"source_revision": None},
        {"source_revision": 0},
        {"source_revision": 2},
        {"source_revision": True},
        {"restoration_kind": "unknown"},
        {"restoration_kind": "editorial"},
    ],
)
def test_invalid_restore(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(event(E.RESTORED), **changes)


@pytest.mark.parametrize("kind", [ActorType.SERVICE, ActorType.AUTOMATED])
def test_execution_context(kind: ActorType) -> None:
    technical = ExecutionActor(f"{kind}:catalog-import", kind)
    assert audit_actor(None, technical) == (technical.actor_id, kind)
    assert replace(event(), actor_id=technical.actor_id, actor_type=kind).actor_type == kind
    with pytest.raises(ValueError):
        audit_actor(actor(), technical)


@pytest.mark.parametrize(
    "identifier,kind",
    [
        ("human:catalog-import", ActorType.HUMAN),
        ("oidc:sha256:123", ActorType.SERVICE),
        ("service:user@example.invalid", ActorType.SERVICE),
        ("service:with space", ActorType.SERVICE),
        ("service:", ActorType.SERVICE),
        ("automated:job", ActorType.SERVICE),
    ],
)
def test_technical_namespace_is_controlled(identifier: str, kind: ActorType) -> None:
    with pytest.raises(ValueError):
        ExecutionActor(identifier, kind)


def test_trusted_human_unknown_and_privacy() -> None:
    human = actor()
    assert audit_actor(human) == (human.actor_id, ActorType.HUMAN)
    assert audit_actor(None) == (None, None)
    with pytest.raises(ValueError):
        audit_actor(replace(human, actor_id="forged"))
    with pytest.raises(ValueError):
        audit_actor({"actor_id": human.actor_id})  # type: ignore[arg-type]
    revision = Revision(
        IDENTITY, 1, NOW, Provenance("fixture", "import", "Reason"), PROFILE_ID, human.actor_id
    )
    observed = content_event(revision, E.CREATED, ActorType.HUMAN)
    fields = asdict(observed)
    assert "issuer" not in fields and "subject" not in fields and "token" not in fields
    assert observed.source == "fixture" and observed.process == "import"
    assert observed.reason == revision.provenance.reason
    assert observed.occurred_at == revision.created_at
    unknown = replace(revision, actor_id=None, provenance=Provenance("legacy", "legacy"))
    assert content_event(unknown, E.CREATED, None).actor_type is None


def test_timestamp_normalized_to_utc() -> None:
    local = NOW.astimezone(timezone(timedelta(hours=-4)))
    observed = replace(event(), occurred_at=local)
    assert observed.occurred_at == NOW and observed.occurred_at.tzinfo is UTC


@pytest.mark.parametrize(
    "profile,release",
    [
        (PROFILE_ID, "1.0"),
        (INSTITUTIONAL_PROFILE_ID, "1.1"),
        (AUTHORITY_PROFILE_ID, "1.0"),
    ],
)
def test_release_resolution_preserves_profile(profile: str, release: str) -> None:
    assert load_legacy_profile(profile).release == release
    assert replace(event(), profile=profile).profile == profile


@pytest.mark.parametrize(
    "source,target,operation",
    [
        (S.DRAFT, S.IN_REVIEW, Op.SUBMIT),
        (S.IN_REVIEW, S.APPROVED, Op.APPROVE),
        (S.APPROVED, S.PUBLISHED, Op.PUBLISH),
        (S.PUBLISHED, S.WITHDRAWN, Op.WITHDRAW),
        (S.WITHDRAWN, S.PUBLISHED, Op.REPUBLISH),
    ],
)
def test_editorial_adapter(source: S, target: S, operation: Op) -> None:
    record = EditorialRecord(
        IDENTITY,
        editorial_state=source,
        approved_revision=1 if source in {S.APPROVED, S.PUBLISHED, S.WITHDRAWN} else None,
        published_revision=1 if source in {S.PUBLISHED, S.WITHDRAWN} else None,
    )
    prepared = prepare_transition(
        record,
        target,
        expected_version=1,
        expected_current_revision=1,
        selected_revision=1,
        actor_id=actor().actor_id,
        occurred_at=NOW,
        justification="Synthetic decision",
        readiness=PublicationReadiness(IDENTITY, 1, PROFILE_ID),
    )
    revision = Revision(IDENTITY, 1, NOW, Provenance("fixture", "test"), PROFILE_ID)
    observed = editorial_event(
        prepared.event, revision, Provenance("fixture", "editorial"), actor=actor()
    )
    assert observed.previous_state == source and observed.new_state == target
    assert observed.revision_number == 1 and observed.previous_version == 1
    if operation == Op.REPUBLISH:
        assert observed.restoration_kind == RestorationKind.EDITORIAL
        assert observed.source_revision is None
    with pytest.raises(ValueError):
        editorial_event(
            replace(prepared.event, actor_id="forged"),
            revision,
            Provenance("fixture", "editorial"),
            actor=actor(),
        )


@pytest.mark.parametrize(
    "role,permission,allowed",
    [
        ("libris-reader", P.METADATA_REVISION_READ, False),
        ("libris-reader", P.METADATA_AUDIT_READ, False),
        ("libris-cataloger", P.METADATA_REVISION_READ, True),
        ("libris-cataloger", P.METADATA_AUDIT_READ, False),
        ("libris-authority-manager", P.METADATA_AUDIT_READ, False),
        ("libris-reviewer", P.METADATA_AUDIT_READ, True),
        ("libris-publisher", P.METADATA_AUDIT_READ, True),
        ("libris-admin", P.METADATA_AUDIT_READ, True),
        ("unknown", P.METADATA_AUDIT_READ, False),
    ],
)
def test_history_permission_policy(role: str, permission: P, allowed: bool) -> None:
    history = HistoryService(None)  # type: ignore[arg-type]
    if allowed:
        history._authorize(actor(role), permission)
    else:
        with pytest.raises(PermissionDenied):
            history._authorize(actor(role), permission)


@pytest.mark.parametrize(
    "operation",
    ["read_revision", "list_revisions", "list_events", "read_event", "compare_revisions"],
)
def test_history_denial_precedes_storage(operation: str) -> None:
    history = HistoryService(None)  # type: ignore[arg-type]
    reader = actor("libris-reader")
    calls = {
        "read_revision": lambda: history.read_revision(IDENTITY.internal_id, actor=reader),
        "list_revisions": lambda: history.list_revisions(IDENTITY.internal_id, actor=reader),
        "list_events": lambda: history.list_events(IDENTITY.internal_id, actor=reader),
        "read_event": lambda: history.read_event(IDENTITY.internal_id, uuid4(), actor=reader),
        "compare_revisions": lambda: history.compare_revisions(
            IDENTITY.internal_id, 1, 2, actor=reader
        ),
    }
    with pytest.raises(PermissionDenied):
        asyncio.run(calls[operation]())


def test_service_rejects_payload_actor_before_storage() -> None:
    service = BibliographicService(None)  # type: ignore[arg-type]
    with pytest.raises(PermissionDenied):
        asyncio.run(
            service.create(
                IDENTITY, Graph(), Provenance("test", "test"), actor={"actor_id": "forged"}
            )
        )  # type: ignore[arg-type]


@pytest.mark.parametrize("limit", [0, -1, 201, True, 1.5])
def test_query_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        validate_page(limit)


@pytest.mark.parametrize("renamed", [False, True])
def test_isomorphic_blank_nodes_and_order(renamed: bool) -> None:
    left = parse_document('<urn:r> <urn:p> [<urn:name> "Name"@pt; <urn:count> 2].', "turtle")
    right = Graph()
    for s, p, o in reversed(list(left)):
        right.add(
            (
                BNode("renamed") if renamed and isinstance(s, BNode) else s,
                p,
                BNode("renamed") if renamed and isinstance(o, BNode) else o,
            )
        )
    before = set(left)
    assert compare_graphs(left, right).is_empty
    assert set(left) == before
    assert compare_graphs(
        left, parse_document(serialize_document(right, "json-ld"), "json-ld")
    ).is_empty


@pytest.mark.parametrize(
    "previous,current",
    [
        (Literal("old"), Literal("new")),
        (Literal("name", lang="pt"), Literal("name", lang="en")),
        (Literal("1", datatype=XSD.string), Literal("1", datatype=XSD.integer)),
        (Literal("same"), URIRef("urn:same")),
    ],
)
def test_diff_preserves_term_details(previous: Literal, current: Literal) -> None:
    left, right = Graph(), Graph()
    left.add((URIRef("urn:s"), URIRef("urn:p"), previous))
    right.add((URIRef("urn:s"), URIRef("urn:p"), current))
    delta = compare_graphs(left, right)
    assert len(delta.added) == len(delta.removed) == 1
    assert delta.added[0].object != delta.removed[0].object


def test_add_remove_and_structurally_changed_blank_nodes() -> None:
    left = parse_document('<urn:r> <urn:p> [<urn:name> "old"].', "turtle")
    right = parse_document('<urn:r> <urn:p> [<urn:name> "new"].', "turtle")
    delta = compare_graphs(left, right)
    assert delta.added and delta.removed
    renamed = parse_document('<urn:r> <urn:p> _:different. _:different <urn:name> "new".', "turtle")
    assert compare_graphs(left, renamed) == delta
    empty = Graph()
    assert len(compare_graphs(empty, left).added) == len(left)
    assert len(compare_graphs(left, empty).removed) == len(left)
    assert compare_graphs(empty, empty).is_empty


@pytest.mark.parametrize(
    "changes",
    [
        {"number": 0},
        {"number": -1},
        {"number": True},
        {"created_at": NOW.replace(tzinfo=None)},
        {"profile": " "},
    ],
)
def test_revision_invariants(changes: dict[str, object]) -> None:
    revision = Revision(IDENTITY, 1, NOW, Provenance("fixture", "test"), PROFILE_ID)
    with pytest.raises(ValueError):
        replace(revision, **changes)


@pytest.mark.parametrize("operation", [Op.RETURN, Op.ARCHIVE])
def test_governed_editorial_operations_are_explicit(operation: Op) -> None:
    source = S.IN_REVIEW if operation == Op.RETURN else S.WITHDRAWN
    target = S.DRAFT if operation == Op.RETURN else S.ARCHIVED
    record = EditorialRecord(
        IDENTITY,
        editorial_state=source,
        approved_revision=1 if source == S.WITHDRAWN else None,
        published_revision=1 if source == S.WITHDRAWN else None,
    )
    change = prepare_transition(
        record,
        target,
        expected_version=1,
        expected_current_revision=1,
        selected_revision=1,
        actor_id=actor().actor_id,
        occurred_at=NOW,
        justification="Synthetic decision",
    )
    revision = Revision(IDENTITY, 1, NOW, Provenance("fixture", "test"), PROFILE_ID)
    event = editorial_event(change.event, revision, Provenance("fixture", "test"), actor=actor())
    assert event.event_type == (E.RETURNED if operation == Op.RETURN else E.ARCHIVED)
    assert event.previous_state == source and event.new_state == target
    assert event.reason == "Synthetic decision"


def test_supersession_requires_distinct_successor_and_reason() -> None:
    with pytest.raises(ValueError):
        replace(event(E.SUPERSEDED), related_resource_id=IDENTITY.internal_id)
    with pytest.raises(ValueError):
        replace(event(E.SUPERSEDED), reason=None)


def test_withdrawal_requires_reason_and_record_scope() -> None:
    with pytest.raises(ValueError):
        replace(event(E.WITHDRAWN), reason=None)
    with pytest.raises(ValueError):
        replace(event(E.WITHDRAWN), scope=EditorialScope.CANDIDATE)


def test_semantic_comparison_cyclic_and_symmetric_blank_nodes() -> None:
    left = parse_document("_:a <urn:p> _:b. _:b <urn:p> _:a. <urn:r> <urn:p> _:a, _:b.", "turtle")
    right = parse_document("<urn:r> <urn:p> _:y, _:x. _:x <urn:p> _:y. _:y <urn:p> _:x.", "turtle")
    assert compare_graphs(left, right).is_empty
