"""Audit integrity on a real, dedicated PostgreSQL database migrated to HEAD."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from libris.modules.bibliographic.application.audit import editorial_event
from libris.modules.bibliographic.application.history import HistoryService
from libris.modules.bibliographic.application.service import BibliographicService
from libris.modules.bibliographic.domain.audit import ActorType, ExecutionActor
from libris.modules.bibliographic.domain.audit import AuditEventType as E
from libris.modules.bibliographic.domain.editorial import (
    EditorialRecord,
    prepare_transition,
)
from libris.modules.bibliographic.domain.editorial import (
    EditorialState as S,
)
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    StaleRevisionError,
)
from libris.modules.bibliographic.infrastructure.audit import (
    AuditStore,
    SemanticAuditEvent,
)
from libris.modules.bibliographic.infrastructure.persistence import (
    SemanticResource,
    SemanticRevision,
)
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.bibliographic.infrastructure.validation import (
    INSTITUTIONAL_PROFILE_ID,
)
from libris.modules.identity.application.authorization import TechnicalContext
from libris.modules.identity.domain.authorization import Operation
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.domain.permissions import Permission as ContextPermission
from libris.modules.identity.infrastructure.role_mapper import map_permissions
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, RDF
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_CONTEXT = TechnicalContext(
    "service:regression-fixture",
    frozenset(
        {
            ContextPermission.BIBLIOGRAPHIC_CREATE,
            ContextPermission.BIBLIOGRAPHIC_UPDATE,
            ContextPermission.BIBLIOGRAPHIC_READ,
            ContextPermission.AUTHORITY_READ,
            ContextPermission.AUTHORITY_MANAGE,
            ContextPermission.METADATA_REVISION_READ,
        }
    ),
    frozenset(
        {
            Operation.BIBLIOGRAPHIC_CREATE,
            Operation.BIBLIOGRAPHIC_UPDATE,
            Operation.AUTHORITY_MANAGE,
            Operation.METADATA_REVISION_READ,
            Operation.BIBLIOGRAPHIC_READ_EDITORIAL,
            Operation.AUTHORITY_READ_EDITORIAL,
        }
    ),
    "pytest:isolated-regression",
)


pytestmark = pytest.mark.integration


def actor(role: str = "libris-admin") -> AuthenticatedActor:
    issuer, subject = "https://issuer.example.invalid", "synthetic-subject"
    roles = frozenset({role})
    return AuthenticatedActor(
        actor_identifier(issuer, subject), issuer, subject, roles, map_permissions(roles)
    )


def graph_for(identity: ResourceIdentity) -> Graph:
    graph = Graph()
    root, title = URIRef(identity.uri), BNode()
    for triple in [
        (root, RDF.type, BF.Work),
        (root, BF.title, title),
        (title, RDF.type, BF.Title),
        (title, BF.mainTitle, Literal("Synthetic", lang="pt")),
    ]:
        graph.add(triple)
    return graph


@asynccontextmanager
async def context() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], BibliographicService]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL descartável migrado para HEAD.")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("Integração exige PostgreSQL real, não SQLite.")
    engine = create_async_engine(url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        yield sessions, BibliographicService(sessions)
    finally:
        await engine.dispose()


def test_content_history_restore_and_authorization() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
            graph = graph_for(identity)
            provenance = Provenance("fixture:synthetic", "test:create", "Synthetic reason")
            human = actor()
            first = await service.create(
                identity, graph, provenance, profile=INSTITUTIONAL_PROFILE_ID, actor=human
            )
            graph.add((URIRef(identity.uri), DCTERMS.description, Literal("New content")))
            second = await service.revise(
                identity.internal_id, 1, graph, provenance, context=TEST_CONTEXT
            )
            history = HistoryService(sessions)
            restored = await service.restore_content(
                identity.internal_id,
                1,
                2,
                Provenance("history:r1", "test:restore", "Undo synthetic change"),
                actor=human,
            )
            assert [first.number, second.number, restored.number] == [1, 2, 3]
            assert first.identity == restored.identity == identity
            assert restored.profile == first.profile
            old, old_graph = await history.read_revision(identity.internal_id, 1, actor=human)
            current, current_graph = await history.read_revision(identity.internal_id, actor=human)
            assert old == first and current == restored and isomorphic(old_graph, current_graph)
            assert (
                await history.compare_revisions(identity.internal_id, 1, 3, actor=human)
            ).is_empty
            assert (await history.compare_revisions(identity.internal_id, 1, 2, actor=human)).added
            entries = await history.list_revisions(identity.internal_id, actor=human, limit=2)
            assert [entry.revision.number for entry in entries] == [1, 2]
            assert all(entry.content_event_recorded for entry in entries)
            page = await history.list_revisions(identity.internal_id, actor=human, after_number=2)
            assert [entry.revision.number for entry in page] == [3]
            events = await history.list_events(identity.internal_id, actor=human)
            assert [event.event_type for event in events] == [E.CREATED, E.REVISED, E.RESTORED]
            assert [event.actor_type for event in events] == [
                ActorType.HUMAN,
                ActorType.SERVICE,
                ActorType.HUMAN,
            ]
            assert events[0].actor_id == human.actor_id and events[0].reason == provenance.reason
            assert events[1].actor_id == TEST_CONTEXT.actor_id and events[2].source_revision == 1
            assert all(event.occurred_at.utcoffset().total_seconds() == 0 for event in events)
            assert (
                await history.read_event(identity.internal_id, events[0].event_id, actor=human)
                == events[0]
            )
            with pytest.raises(LookupError):
                await history.read_event(uuid4(), events[0].event_id, actor=human)
            with pytest.raises(PermissionDenied):
                await history.list_events(identity.internal_id, actor=actor("libris-cataloger"))
            with pytest.raises(PermissionDenied):
                await service.restore_content(
                    identity.internal_id, 1, 3, provenance, actor=actor("libris-reviewer")
                )
            with pytest.raises(StaleRevisionError):
                await service.restore_content(identity.internal_id, 1, 2, provenance, actor=human)
            with pytest.raises(LookupError):
                await history.read_revision(identity.internal_id, 999, actor=human)
            assert (await service.read(identity.internal_id, context=TEST_CONTEXT))[0].number == 3

    asyncio.run(scenario())


def test_audit_failure_rolls_back_resource_revision_and_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
            graph, provenance = graph_for(identity), Provenance("fixture", "test")
            real_append = service.audit.append

            async def fail(session: AsyncSession, event: object) -> None:
                # Insert first to prove the event itself rolls back too.
                await real_append(session, event)  # type: ignore[arg-type]
                raise RuntimeError("synthetic failure after audit INSERT")

            monkeypatch.setattr(service.audit, "append", fail)
            with pytest.raises(RuntimeError):
                await service.create(identity, graph, provenance, context=TEST_CONTEXT)
            async with sessions() as session:
                assert await session.get(SemanticResource, identity.internal_id) is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SemanticRevision)
                        .where(SemanticRevision.resource_id == identity.internal_id)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SemanticAuditEvent)
                        .where(SemanticAuditEvent.resource_id == identity.internal_id)
                    )
                    == 0
                )
            monkeypatch.setattr(service.audit, "append", real_append)
            await service.create(identity, graph, provenance, context=TEST_CONTEXT)
            monkeypatch.setattr(service.audit, "append", fail)
            with pytest.raises(RuntimeError):
                await service.revise(
                    identity.internal_id, 1, graph, provenance, context=TEST_CONTEXT
                )
            assert (await service.read(identity.internal_id, context=TEST_CONTEXT))[0].number == 1
            async with sessions() as session:
                events = await AuditStore().list(session, identity.internal_id)
                assert len(events) == 1 and events[0].event_type == E.CREATED
                with pytest.raises(LookupError):
                    await service.read(identity.internal_id, 2, context=TEST_CONTEXT)

    asyncio.run(scenario())


def test_real_concurrent_transactions_leave_one_revision_and_event() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
            graph, provenance = graph_for(identity), Provenance("fixture", "test")
            await service.create(identity, graph, provenance, context=TEST_CONTEXT)
            results = await asyncio.gather(
                service.revise(identity.internal_id, 1, graph, provenance, context=TEST_CONTEXT),
                service.revise(identity.internal_id, 1, graph, provenance, context=TEST_CONTEXT),
                return_exceptions=True,
            )
            assert sum(isinstance(result, StaleRevisionError) for result in results) == 1
            assert sum(not isinstance(result, Exception) for result in results) == 1
            async with sessions() as session:
                events = await AuditStore().list(session, identity.internal_id)
                assert [event.revision_number for event in events] == [1, 2]
                assert [event.event_type for event in events] == [E.CREATED, E.REVISED]
                revisions = (
                    await session.scalars(
                        select(SemanticRevision.number)
                        .where(SemanticRevision.resource_id == identity.internal_id)
                        .order_by(SemanticRevision.number)
                    )
                ).all()
                assert revisions == [1, 2]

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
def test_postgres_blocks_audit_mutation(operation: str) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
            await service.create(
                identity, graph_for(identity), Provenance("fixture", "test"), context=TEST_CONTEXT
            )
            statements = {
                "UPDATE": "UPDATE experimental_semantic_audit_events SET source='tampered' "
                "WHERE resource_id=:id",
                "DELETE": "DELETE FROM experimental_semantic_audit_events WHERE resource_id=:id",
                "TRUNCATE": "TRUNCATE experimental_semantic_audit_events",
            }
            with pytest.raises(DBAPIError, match="imutáveis"):
                async with sessions() as session, session.begin():
                    await session.execute(text(statements[operation]), {"id": identity.internal_id})
            async with sessions() as session:
                assert len(await AuditStore().list(session, identity.internal_id)) == 1

    asyncio.run(scenario())


def test_deterministic_order_and_events_without_content_revision() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
            first = await service.create(
                identity, graph_for(identity), Provenance("fixture", "test"), context=TEST_CONTEXT
            )
            human = actor()
            change = prepare_transition(
                EditorialRecord(identity),
                S.IN_REVIEW,
                expected_version=1,
                expected_current_revision=1,
                selected_revision=1,
                actor_id=human.actor_id,
                occurred_at=datetime.now(UTC),
            )
            prepared = editorial_event(
                change.event, first, Provenance("fixture", "test:editorial"), actor=human
            )
            # Store envelope capability only: no claim that editorial state was persisted.
            lower_id, upper_id = sorted((uuid4(), uuid4()))
            lower = replace(prepared, event_id=lower_id)
            upper = replace(prepared, event_id=upper_id)
            async with sessions() as session, session.begin():
                await AuditStore().append(session, upper)
                await AuditStore().append(session, lower)
            history = HistoryService(sessions)
            events = await history.list_events(identity.internal_id, actor=human)
            assert [event.event_id for event in events][-2:] == [lower.event_id, upper.event_id]
            tail = await history.list_events(
                identity.internal_id, actor=human, after=(lower.occurred_at, lower.event_id)
            )
            assert tail == (upper,)
            assert (await service.read(identity.internal_id, context=TEST_CONTEXT))[0].number == 1
            with pytest.raises(DBAPIError):
                async with sessions() as session, session.begin():
                    await AuditStore().append(session, lower)
            with pytest.raises(ValueError):
                async with sessions() as session, session.begin():
                    await AuditStore().append(session, replace(lower, profile="urn:wrong"))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"resource_id": uuid4()},
        {"revision_number": 999},
        {"event_type": "record.arbitrary"},
        {"actor_id": "forged", "actor_type": "human"},
        {"actor_type": "service"},
        {"event_type": "record.superseded"},
        {"event_type": "record.restored"},
        {"event_type": "record.approved"},
        {"revision_number": 0},
        {"related_resource_id": uuid4()},
        {"event_type": "record.restored", "restoration_kind": "content", "source_revision": 999},
    ],
)
def test_direct_sql_constraints_and_referential_integrity(changes: dict[str, object]) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
            await service.create(
                identity, graph_for(identity), Provenance("fixture", "test"), context=TEST_CONTEXT
            )
            values = dict(
                event_id=uuid4(),
                resource_id=identity.internal_id,
                event_type="record.created",
                revision_number=1,
                occurred_at=datetime.now(UTC),
                source="fixture",
                process="test",
            )
            values.update(changes)
            with pytest.raises(DBAPIError):
                async with sessions() as session, session.begin():
                    await session.execute(insert(SemanticAuditEvent).values(**values))
            async with sessions() as session:
                assert len(await AuditStore().list(session, identity.internal_id)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", [ActorType.SERVICE, ActorType.AUTOMATED])
def test_technical_actor_persistence(kind: ActorType) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
            technical = ExecutionActor(f"{kind}:synthetic-import", kind)
            created = await service.create(
                identity,
                graph_for(identity),
                Provenance("fixture", "test"),
                execution_actor=technical,
                context=TechnicalContext(
                    technical.actor_id,
                    frozenset({ContextPermission.BIBLIOGRAPHIC_CREATE}),
                    frozenset({Operation.BIBLIOGRAPHIC_CREATE}),
                    "pytest:technical-audit",
                ),
            )
            async with sessions() as session:
                events = await AuditStore().list(session, identity.internal_id)
                assert events[0].actor_type == kind and events[0].actor_id == created.actor_id

    asyncio.run(scenario())
