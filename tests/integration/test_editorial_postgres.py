"""Persistent editorial governance: independent sessions, failures and concurrent writes."""

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from libris.modules.bibliographic.application.editorial import EditorialService
from libris.modules.bibliographic.application.editorial_postgres import (
    PostgresEditorialTransactions,
)
from libris.modules.bibliographic.application.history import (
    EditorialHistory,
    HistoryService,
)
from libris.modules.bibliographic.domain.audit import AuditEvent
from libris.modules.bibliographic.domain.audit import AuditEventType as E
from libris.modules.bibliographic.domain.editorial import (
    EditorialScope,
    PreparedTransition,
)
from libris.modules.bibliographic.domain.editorial import EditorialState as S
from libris.modules.bibliographic.domain.errors import CatalogError
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    Revision,
    StaleRevisionError,
)
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.infrastructure.audit import AuditStore
from libris.modules.bibliographic.infrastructure.editorial import (
    EditorialStore,
    SemanticEditorialState,
)
from libris.modules.bibliographic.infrastructure.persistence import (
    RevisionStore,
    SemanticResource,
    SemanticRevision,
)
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
)
from libris.modules.identity.application.authorization import TechnicalContext
from libris.modules.identity.domain.authorization import Operation
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.models import AuthenticatedActor
from libris.modules.identity.domain.permissions import Permission as ContextPermission
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import RDF, RDFS, SKOS
from sqlalchemy import insert, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_audit_postgres import actor, context, graph_for

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
PROVENANCE = Provenance("fixture:issue43", "test:editorial")


def transactions(
    sessions: async_sessionmaker[AsyncSession],
    *,
    human: AuthenticatedActor | None = None,
    external: Mapping[str, EntityType] | None = None,
) -> PostgresEditorialTransactions:
    return PostgresEditorialTransactions(
        sessions, actor=human or actor(), provenance=PROVENANCE, external_references=external
    )


async def state(sessions: async_sessionmaker[AsyncSession], identifier: UUID) -> EditorialHistory:
    return await HistoryService(sessions).read_editorial(identifier, actor=actor())


async def transition(
    sessions: async_sessionmaker[AsyncSession],
    identifier: UUID,
    target: S,
    *,
    scope: EditorialScope = EditorialScope.RECORD,
    human: AuthenticatedActor | None = None,
    factory: PostgresEditorialTransactions | None = None,
    selected: int | None = None,
    version: int | None = None,
    current: int | None = None,
) -> PreparedTransition:
    prior = (await state(sessions, identifier)).state
    assert prior is not None
    selected = (
        selected
        if selected is not None
        else (
            prior.published_revision
            if target in {S.WITHDRAWN, S.ARCHIVED}
            or (target == S.PUBLISHED and prior.editorial_state == S.WITHDRAWN)
            else prior.current_revision
        )
    )
    assert selected is not None
    return await EditorialService(factory or transactions(sessions, human=human)).transition(
        identifier,
        target,
        actor=human or actor(),
        expected_version=version if version is not None else prior.version,
        expected_current_revision=current if current is not None else prior.current_revision,
        selected_revision=selected,
        scope=scope,
        justification="Synthetic decision"
        if target in {S.DRAFT, S.WITHDRAWN, S.ARCHIVED}
        else None,
    )


async def publish(sessions: async_sessionmaker[AsyncSession], identifier: UUID) -> None:
    for target in (S.IN_REVIEW, S.APPROVED, S.PUBLISHED):
        await transition(sessions, identifier, target)


def test_persistent_workflow_candidate_return_restore_and_archive() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://issue43.example.invalid/resources")
            identifier = identity.internal_id
            graph = graph_for(identity)
            await service.create(
                identity, graph, PROVENANCE, profile=INSTITUTIONAL_PROFILE_ID, context=TEST_CONTEXT
            )
            initial = await state(sessions, identifier)
            assert initial.state.version == 1 and initial.state.current_revision == 1
            await publish(sessions, identifier)
            assert (await state(sessions, identifier)).state.version == 4
            graph.add((URIRef(identity.uri), BF.note, BNode("note")))
            graph.add((BNode("note"), RDF.type, BF.Note))
            graph.add(
                (
                    BNode("note"),
                    URIRef("http://www.w3.org/2000/01/rdf-schema#label"),
                    Literal("Candidate"),
                )
            )
            await service.revise(
                identifier, 1, graph, PROVENANCE, expected_editorial_version=4, context=TEST_CONTEXT
            )
            public = await service.read_public(identifier)
            assert public[0].number == 1 and isomorphic(public[1], graph_for(identity))
            candidate = (await state(sessions, identifier)).state
            assert candidate.current_revision == 2 and candidate.candidate_state == S.DRAFT
            for target in (S.IN_REVIEW, S.APPROVED, S.DRAFT):
                await transition(sessions, identifier, target, scope=EditorialScope.CANDIDATE)
            restored = await service.restore_content(identifier, 1, 2, PROVENANCE, actor=actor())
            assert restored.number == 3 and restored.identity == identity
            assert isomorphic(
                (await service.read(identifier, 3, context=TEST_CONTEXT))[1], graph_for(identity)
            )
            assert (await service.read_public(identifier))[0].number == 1
            for target in (S.IN_REVIEW, S.APPROVED, S.PUBLISHED):
                await transition(sessions, identifier, target, scope=EditorialScope.CANDIDATE)
            assert (await service.read_public(identifier))[0].number == 3
            await transition(sessions, identifier, S.WITHDRAWN)
            assert await service.read_public(identifier) is None
            await transition(sessions, identifier, S.PUBLISHED)
            await transition(sessions, identifier, S.WITHDRAWN)
            await transition(sessions, identifier, S.ARCHIVED)
            final = await state(sessions, identifier)
            assert final.state.editorial_state == S.ARCHIVED
            assert final.current.identity == identity
            assert isomorphic(
                (await service.read(identifier, 1, context=TEST_CONTEXT))[1], graph_for(identity)
            )
            types = [e.event_type for e in final.events]
            assert types.count(E.RETURNED) == 1 and types.count(E.ARCHIVED) == 1
            assert types.count(E.RESTORED) == 2
            editorial = [e for e in final.events if e.new_version is not None]
            assert len(editorial) == len({e.new_version for e in editorial}) == 13
            assert all(e.actor_id == actor().actor_id for e in editorial)
            assert all(e.source == PROVENANCE.source for e in editorial)
            with pytest.raises(CatalogError):
                await service.revise(identifier, 3, graph, PROVENANCE, context=TEST_CONTEXT)
            assert await state(sessions, identifier) == final
            with pytest.raises(PermissionDenied):
                await HistoryService(sessions).read_editorial(
                    identifier, actor=actor("libris-reader")
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["permission", "cas", "audit", "exception", "integrity", "audit_integrity"]
)
def test_transition_failures_roll_back_in_fresh_session(failure: str) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://issue43.example.invalid/resources")
            await service.create(
                identity,
                graph_for(identity),
                PROVENANCE,
                profile=INSTITUTIONAL_PROFILE_ID,
                context=TEST_CONTEXT,
            )
            identifier = identity.internal_id
            before = await state(sessions, identifier)
            factory = transactions(sessions)

            class FailingAudit(AuditStore):
                async def append(self, session: AsyncSession, event: AuditEvent) -> None:
                    if failure == "integrity":
                        event = replace(event, revision_number=999)
                    await super().append(session, event)
                    if failure == "audit_integrity":
                        await session.execute(
                            text(
                                "INSERT INTO experimental_semantic_audit_events "
                                "SELECT * FROM experimental_semantic_audit_events "
                                "WHERE event_id=:id"
                            ),
                            {"id": event.event_id},
                        )
                    if failure in {"audit", "exception"}:
                        raise RuntimeError("Synthetic post-insert failure")

            if failure in {"audit", "exception", "integrity", "audit_integrity"}:
                factory.audit = FailingAudit()
            expected = (CatalogError, RuntimeError, LookupError, DBAPIError)
            with pytest.raises(expected):
                await transition(
                    sessions,
                    identifier,
                    S.IN_REVIEW,
                    factory=factory,
                    human=actor("libris-reader") if failure == "permission" else None,
                    version=9 if failure == "cas" else None,
                )
            assert await state(sessions, identifier) == before
            assert (await service.read(identifier, context=TEST_CONTEXT))[0].number == 1

    asyncio.run(scenario())


def test_state_and_revision_constraints_and_conditional_zero_row_conflict() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://issue43.example.invalid/resources")
            await service.create(identity, graph_for(identity), PROVENANCE, context=TEST_CONTEXT)
            before = await state(sessions, identity.internal_id)
            for values in (
                {"version": 0},
                {"approved_revision": 99},
                {"editorial_state": "published"},
                {"current_revision": 999},
                {"candidate_state": "draft"},
            ):
                with pytest.raises(DBAPIError):
                    async with sessions() as session, session.begin():
                        await session.execute(
                            update(SemanticEditorialState)
                            .where(SemanticEditorialState.resource_id == identity.internal_id)
                            .values(**values)
                        )
                assert await state(sessions, identity.internal_id) == before
            async with sessions() as session, session.begin():
                stale = replace(before.state, version=99)
                with pytest.raises(CatalogError):
                    await EditorialStore().save(session, stale, replace(stale, version=100))
            assert await state(sessions, identity.internal_id) == before

    asyncio.run(scenario())


def test_legacy_initialization_and_denied_attempt_do_not_fabricate_history() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://legacy43.example.invalid/resources")
            graph = graph_for(identity)
            from libris.modules.bibliographic.application.service import (
                prepare_document,
            )
            from libris.modules.bibliographic.domain.models import Revision

            revision = Revision(
                identity, 1, datetime.now(UTC), PROVENANCE, INSTITUTIONAL_PROFILE_ID
            )
            async with sessions() as session, session.begin():
                await RevisionStore().create(session, identity)
                await RevisionStore().save(
                    session, revision, prepare_document(graph, identity.uri, revision.profile)
                )
            before = await state(sessions, identity.internal_id)
            assert before.state is None and before.events == ()
            assert await service.read_public(identity.internal_id) is None
            for human in (actor("libris-reader"), actor()):
                factory = transactions(sessions, human=human)

                async def submit() -> PreparedTransition:
                    return await EditorialService(factory).transition(
                        identity.internal_id,
                        S.IN_REVIEW,
                        actor=human,
                        expected_version=1,
                        expected_current_revision=1,
                        selected_revision=1,
                    )

                if human == actor("libris-reader"):
                    with pytest.raises(CatalogError):
                        await submit()
                    assert await state(sessions, identity.internal_id) == before
                else:
                    await submit()
            after = await state(sessions, identity.internal_id)
            assert after.state.version == 2 and after.events[0].event_type == E.SUBMITTED
            assert len(after.events) == 1 and after.current == revision

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["Work", "Instance", "Item", "Agent", "Subject"])
def test_publication_all_supported_types_with_local_reference_integrity(kind: str) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://types43.example.invalid/resources")
            graph = graph_for(identity)
            profile = INSTITUTIONAL_PROFILE_ID
            if kind in {"Instance", "Item"}:
                parent = ResourceIdentity.create("https://types43.example.invalid/resources")
                parent_graph = graph_for(parent)
                if kind == "Item":
                    work = ResourceIdentity.create("https://types43.example.invalid/resources")
                    await service.create(
                        work,
                        graph_for(work),
                        PROVENANCE,
                        profile=INSTITUTIONAL_PROFILE_ID,
                        context=TEST_CONTEXT,
                    )
                    parent_graph = Graph()
                    parent_graph.add((URIRef(parent.uri), RDF.type, BF.Instance))
                    parent_graph.add((URIRef(parent.uri), BF.instanceOf, URIRef(work.uri)))
                await service.create(
                    parent,
                    parent_graph,
                    PROVENANCE,
                    profile=INSTITUTIONAL_PROFILE_ID,
                    context=TEST_CONTEXT,
                )
                graph = Graph()
                graph.add((URIRef(identity.uri), RDF.type, BF[kind]))
                graph.add(
                    (
                        URIRef(identity.uri),
                        BF.instanceOf if kind == "Instance" else BF.itemOf,
                        URIRef(parent.uri),
                    )
                )
                if kind == "Item":
                    identifier = BNode()
                    graph.add((URIRef(identity.uri), BF.identifiedBy, identifier))
                    graph.add((identifier, RDF.type, BF.Local))
                    graph.add((identifier, RDF.value, Literal("synthetic43")))
            elif kind in {"Agent", "Subject"}:
                profile = AUTHORITY_PROFILE_ID
                graph = Graph()
                if kind == "Agent":
                    graph.add((URIRef(identity.uri), RDF.type, BF.Person))
                    graph.add((URIRef(identity.uri), RDFS.label, Literal("Synthetic")))
                else:
                    graph.add((URIRef(identity.uri), RDF.type, SKOS.Concept))
                    graph.add(
                        (URIRef(identity.uri), SKOS.prefLabel, Literal("Synthetic", lang="pt"))
                    )
                    graph.add((URIRef(identity.uri), SKOS.inScheme, URIRef("urn:synthetic:scheme")))
            await service.create(identity, graph, PROVENANCE, profile=profile, context=TEST_CONTEXT)
            await publish(sessions, identity.internal_id)
            public = await service.read_public(identity.internal_id)
            assert public[0].identity == identity and isomorphic(public[1], graph)

    asyncio.run(scenario())


def test_unclassified_required_reference_blocks_publication_atomically() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://ref43.example.invalid/resources")
            graph = Graph()
            graph.add((URIRef(identity.uri), RDF.type, BF.Instance))
            graph.add((URIRef(identity.uri), BF.instanceOf, URIRef("urn:missing:work")))
            await service.create(
                identity, graph, PROVENANCE, profile=INSTITUTIONAL_PROFILE_ID, context=TEST_CONTEXT
            )
            await transition(sessions, identity.internal_id, S.IN_REVIEW)
            await transition(sessions, identity.internal_id, S.APPROVED)
            before = await state(sessions, identity.internal_id)
            with pytest.raises(CatalogError):
                await transition(sessions, identity.internal_id, S.PUBLISHED)
            assert await state(sessions, identity.internal_id) == before
            assert await service.read_public(identity.internal_id) is None

    asyncio.run(scenario())


def test_invalid_legacy_shacl_publication_preserves_diagnostics_and_state() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://invalid43.example.invalid/resources")
            # Seed exactly as an old importer: immutable incomplete Work, no fabricated validation.
            async with sessions() as session, session.begin():
                await session.execute(
                    insert(SemanticResource).values(
                        id=identity.internal_id, uri=identity.uri, current_revision=1
                    )
                )
                await session.execute(
                    insert(SemanticRevision).values(
                        resource_id=identity.internal_id,
                        number=1,
                        document=[{"@id": identity.uri, "@type": [str(BF.Work)]}],
                        created_at=datetime.now(UTC),
                        source="legacy",
                        process="legacy",
                        profile=INSTITUTIONAL_PROFILE_ID,
                    )
                )
            factory = transactions(sessions)
            await EditorialService(factory).transition(
                identity.internal_id,
                S.IN_REVIEW,
                actor=actor(),
                expected_version=1,
                expected_current_revision=1,
                selected_revision=1,
            )
            await transition(sessions, identity.internal_id, S.APPROVED)
            before = await state(sessions, identity.internal_id)
            from libris.modules.bibliographic.application.editorial import (
                CatalogPublicationRejected,
            )

            with pytest.raises(CatalogPublicationRejected) as error:
                await transition(sessions, identity.internal_id, S.PUBLISHED)
            assert error.value.result.conforms is False and error.value.result.issues
            assert await state(sessions, identity.internal_id) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "race",
    [
        "approval_revision",
        "publication_candidate",
        "two_publications",
        "withdraw_republish",
        "restore_revision",
    ],
)
def test_concurrent_sessions_reject_stale_decisions(race: str) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://race43.example.invalid/resources")
            identifier = identity.internal_id
            graph = graph_for(identity)
            await service.create(
                identity, graph, PROVENANCE, profile=INSTITUTIONAL_PROFILE_ID, context=TEST_CONTEXT
            )
            if race == "approval_revision":
                await transition(sessions, identifier, S.IN_REVIEW)
            elif race == "two_publications":
                await transition(sessions, identifier, S.IN_REVIEW)
                await transition(sessions, identifier, S.APPROVED)
            elif race in {"publication_candidate", "withdraw_republish"}:
                await publish(sessions, identifier)
                if race == "publication_candidate":
                    await service.revise(identifier, 1, graph, PROVENANCE, context=TEST_CONTEXT)
                    await transition(
                        sessions, identifier, S.IN_REVIEW, scope=EditorialScope.CANDIDATE
                    )
                    await transition(
                        sessions, identifier, S.APPROVED, scope=EditorialScope.CANDIDATE
                    )
            before = await state(sessions, identifier)
            record = before.state

            async def decision(
                target: S, scope: EditorialScope = EditorialScope.RECORD
            ) -> PreparedTransition:
                return await transition(
                    sessions,
                    identifier,
                    target,
                    scope=scope,
                    version=record.version,
                    current=record.current_revision,
                )

            async def edit() -> Revision:
                return await service.revise(
                    identifier,
                    record.current_revision,
                    graph,
                    PROVENANCE,
                    expected_editorial_version=record.version,
                    context=TEST_CONTEXT,
                )

            if race == "approval_revision":
                calls = [decision(S.APPROVED), edit()]
            elif race == "publication_candidate":
                calls = [decision(S.PUBLISHED, EditorialScope.CANDIDATE), edit()]
            elif race == "two_publications":
                calls = [decision(S.PUBLISHED), decision(S.PUBLISHED)]
            elif race == "withdraw_republish":
                calls = [decision(S.WITHDRAWN), decision(S.PUBLISHED)]
            else:
                calls = [
                    service.restore_content(
                        identifier,
                        1,
                        1,
                        PROVENANCE,
                        actor=actor(),
                        expected_editorial_version=record.version,
                    ),
                    edit(),
                ]
            results = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), 15)
            assert sum(not isinstance(r, Exception) for r in results) == 1, results
            rejected = next(r for r in results if isinstance(r, Exception))
            assert isinstance(rejected, (CatalogError, StaleRevisionError)), results
            after = await state(sessions, identifier)
            assert after.state.version == record.version + 1
            assert len(after.events) == len(before.events) + 1
            assert after.current.identity == identity
            assert isomorphic(
                (await service.read(identifier, 1, context=TEST_CONTEXT))[1], graph_for(identity)
            )

    asyncio.run(scenario())


def test_local_cycle_rejected_and_external_classification_explicit() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            first = ResourceIdentity.create("https://cycle43.example.invalid/resources")
            second = ResourceIdentity.create("https://cycle43.example.invalid/resources")
            first_graph, second_graph = graph_for(first), graph_for(second)
            first_graph.add((URIRef(first.uri), BF.translationOf, URIRef(second.uri)))
            second_graph.add((URIRef(second.uri), BF.translationOf, URIRef(first.uri)))
            await service.create(
                first,
                first_graph,
                PROVENANCE,
                profile=INSTITUTIONAL_PROFILE_ID,
                context=TEST_CONTEXT,
            )
            await service.create(
                second,
                second_graph,
                PROVENANCE,
                profile=INSTITUTIONAL_PROFILE_ID,
                context=TEST_CONTEXT,
            )
            for target in (S.IN_REVIEW, S.APPROVED):
                await transition(sessions, first.internal_id, target)
            before = await state(sessions, first.internal_id)
            with pytest.raises(CatalogError):
                await transition(sessions, first.internal_id, S.PUBLISHED)
            assert await state(sessions, first.internal_id) == before
            external = ResourceIdentity.create("https://external43.example.invalid/resources")
            graph = Graph()
            graph.add((URIRef(external.uri), RDF.type, BF.Instance))
            graph.add((URIRef(external.uri), BF.instanceOf, URIRef("urn:external:work")))
            await service.create(
                external, graph, PROVENANCE, profile=INSTITUTIONAL_PROFILE_ID, context=TEST_CONTEXT
            )
            for target in (S.IN_REVIEW, S.APPROVED):
                await transition(sessions, external.internal_id, target)

            factory = transactions(sessions, external={"urn:external:work": EntityType.WORK})
            await transition(sessions, external.internal_id, S.PUBLISHED, factory=factory)
            assert (await service.read_public(external.internal_id))[0].number == 1

    asyncio.run(scenario())


def test_local_dependency_cannot_change_between_validation_and_commit() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            work = ResourceIdentity.create("https://gate43.example.invalid/resources")
            instance = ResourceIdentity.create("https://gate43.example.invalid/resources")
            await service.create(
                work,
                graph_for(work),
                PROVENANCE,
                profile=INSTITUTIONAL_PROFILE_ID,
                context=TEST_CONTEXT,
            )
            graph = Graph()
            graph.add((URIRef(instance.uri), RDF.type, BF.Instance))
            graph.add((URIRef(instance.uri), BF.instanceOf, URIRef(work.uri)))
            await service.create(
                instance, graph, PROVENANCE, profile=INSTITUTIONAL_PROFILE_ID, context=TEST_CONTEXT
            )
            for target in (S.IN_REVIEW, S.APPROVED):
                await transition(sessions, instance.internal_id, target)
            entered, release = asyncio.Event(), asyncio.Event()

            class PausedAudit(AuditStore):
                async def append(self, session: AsyncSession, event: AuditEvent) -> None:
                    await super().append(session, event)
                    entered.set()
                    await release.wait()

            factory = transactions(sessions)
            factory.audit = PausedAudit()
            publishing = asyncio.create_task(
                transition(sessions, instance.internal_id, S.PUBLISHED, factory=factory)
            )
            await asyncio.wait_for(entered.wait(), 5)
            revising = asyncio.create_task(
                service.revise(
                    work.internal_id, 1, graph_for(work), PROVENANCE, context=TEST_CONTEXT
                )
            )
            try:
                await asyncio.sleep(0.1)
                assert not revising.done(), "A dependência não pode mudar antes do commit."
            finally:
                release.set()
            published, revised = await asyncio.wait_for(asyncio.gather(publishing, revising), 10)
            assert published.after.public_revision == 1 and revised.number == 2
            assert (await service.read_public(instance.internal_id))[0].number == 1

    asyncio.run(scenario())


def test_content_audit_failure_rolls_back_editorial_and_snapshot() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://rollback43.example.invalid/resources")
            graph = graph_for(identity)
            await service.create(
                identity, graph, PROVENANCE, profile=INSTITUTIONAL_PROFILE_ID, context=TEST_CONTEXT
            )
            await publish(sessions, identity.internal_id)
            before = await state(sessions, identity.internal_id)

            class FailingAudit(AuditStore):
                async def append(self, session: AsyncSession, event: AuditEvent) -> None:
                    await super().append(session, event)
                    raise RuntimeError("Synthetic audit failure")

            service.audit = FailingAudit()
            with pytest.raises(RuntimeError):
                await service.revise(
                    identity.internal_id, 1, graph, PROVENANCE, context=TEST_CONTEXT
                )
            assert await state(sessions, identity.internal_id) == before
            assert (await service.read_public(identity.internal_id))[0].number == 1
            with pytest.raises(LookupError):
                await service.read(identity.internal_id, 2, context=TEST_CONTEXT)

    asyncio.run(scenario())


@pytest.mark.parametrize("relationship", ["contribution", "publication", "held_by"])
def test_publication_structured_links_and_agent_kind(relationship: str) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            agent_identity = ResourceIdentity.create("https://links43.example.invalid/resources")
            agent_graph = Graph()
            agent_graph.add((URIRef(agent_identity.uri), RDF.type, BF.Organization))
            agent_graph.add(
                (URIRef(agent_identity.uri), RDFS.label, Literal("Synthetic organization"))
            )
            await service.create(
                agent_identity,
                agent_graph,
                PROVENANCE,
                profile=AUTHORITY_PROFILE_ID,
                context=TEST_CONTEXT,
            )
            identity = ResourceIdentity.create("https://links43.example.invalid/resources")
            root, agent_node = URIRef(identity.uri), URIRef(agent_identity.uri)
            graph = graph_for(identity)
            if relationship == "contribution":
                contribution = BNode()
                graph.add((root, BF.contribution, contribution))
                graph.add((contribution, RDF.type, BF.Contribution))
                graph.add((contribution, BF.agent, agent_node))
                graph.add((contribution, BF.role, URIRef("urn:synthetic:role:author")))
            else:
                work = ResourceIdentity.create("https://links43.example.invalid/resources")
                await service.create(
                    work,
                    graph_for(work),
                    PROVENANCE,
                    profile=INSTITUTIONAL_PROFILE_ID,
                    context=TEST_CONTEXT,
                )
                graph = Graph()
                if relationship == "publication":
                    graph.add((root, RDF.type, BF.Instance))
                    graph.add((root, BF.instanceOf, URIRef(work.uri)))
                    activity = BNode()
                    graph.add((root, BF.provisionActivity, activity))
                    graph.add((activity, RDF.type, BF.Publication))
                    graph.add((activity, BF.agent, agent_node))
                else:
                    instance = ResourceIdentity.create("https://links43.example.invalid/resources")
                    instance_graph = Graph()
                    instance_graph.add((URIRef(instance.uri), RDF.type, BF.Instance))
                    instance_graph.add((URIRef(instance.uri), BF.instanceOf, URIRef(work.uri)))
                    await service.create(
                        instance,
                        instance_graph,
                        PROVENANCE,
                        profile=INSTITUTIONAL_PROFILE_ID,
                        context=TEST_CONTEXT,
                    )
                    graph.add((root, RDF.type, BF.Item))
                    graph.add((root, BF.itemOf, URIRef(instance.uri)))
                    graph.add((root, BF.heldBy, agent_node))
                    mark = BNode()
                    graph.add((root, BF.identifiedBy, mark))
                    graph.add((mark, RDF.type, BF.Local))
                    graph.add((mark, RDF.value, Literal("synthetic43")))
            await service.create(
                identity, graph, PROVENANCE, profile=INSTITUTIONAL_PROFILE_ID, context=TEST_CONTEXT
            )
            await publish(sessions, identity.internal_id)
            assert isomorphic((await service.read_public(identity.internal_id))[1], graph)

    asyncio.run(scenario())
