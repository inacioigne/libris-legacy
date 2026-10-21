"""Incremental 0003->0004 verification in a separate, initially empty PostgreSQL database."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from libris.modules.bibliographic.application.audit import (
    content_event,
    editorial_event,
)
from libris.modules.bibliographic.application.editorial import EditorialService
from libris.modules.bibliographic.application.editorial_postgres import (
    PostgresEditorialTransactions,
)
from libris.modules.bibliographic.application.history import HistoryService
from libris.modules.bibliographic.application.service import (
    BibliographicService,
    prepare_document,
)
from libris.modules.bibliographic.domain.audit import AuditEventType
from libris.modules.bibliographic.domain.editorial import (
    EditorialRecord,
    EditorialScope,
    EditorialState,
    prepare_transition,
)
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    Revision,
)
from libris.modules.bibliographic.infrastructure.audit import AuditStore
from libris.modules.bibliographic.infrastructure.persistence import RevisionStore
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
)
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, RDF, RDFS, SKOS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_audit_postgres import actor, graph_for
from test_editorial_postgres import publish, state, transition

pytestmark = pytest.mark.integration


def test_editorial_upgrade_from_previous_head_and_safe_downgrade() -> None:
    url = os.environ.get("TEST_EDITORIAL_MIGRATION_DATABASE_URL")
    if not url:
        pytest.skip("TEST_EDITORIAL_MIGRATION_DATABASE_URL exige terceiro banco vazio descartável.")
    if not url.startswith("postgresql+asyncpg://") or url in {
        os.environ.get("TEST_DATABASE_URL"),
        os.environ.get("TEST_MIGRATION_DATABASE_URL"),
    }:
        pytest.fail("Migração exige PostgreSQL descartável exclusivo e vazio.")
    api = Path(__file__).resolve().parents[2] / "apps/api"

    def alembic(*arguments: str, succeeds: bool = True) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=api,
            env=os.environ | {"DATABASE_URL": url},
            capture_output=True,
            text=True,
            check=False,
        )
        if succeeds:
            assert result.returncode == 0, result.stdout + result.stderr
        else:
            assert result.returncode != 0
            assert "Downgrade incompatível" in result.stdout + result.stderr

    async def assert_empty() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema='public'"
                        )
                    )
                    == 0
                ), "Banco deve começar vazio; nunca migrar dados de trabalho."
        finally:
            await engine.dispose()

    asyncio.run(assert_empty())
    alembic("upgrade", "0003_semantic_audit")
    identity = ResourceIdentity(uuid4(), f"https://legacy43.example.invalid/{uuid4()}")
    revision = Revision(
        identity, 1, datetime.now(UTC), Provenance("legacy", "legacy"), INSTITUTIONAL_PROFILE_ID
    )
    document = prepare_document(graph_for(identity), identity.uri, revision.profile)

    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            sessions = async_sessionmaker(engine)
            async with sessions() as session, session.begin():
                await RevisionStore().create(session, identity)
                await RevisionStore().save(session, revision, document)
                await AuditStore().append(
                    session, content_event(revision, AuditEventType.CREATED, None)
                )
                # Valid envelopes accepted by #28; no persisted state and repeated version.
                for _ in range(2):
                    change = prepare_transition(
                        EditorialRecord(identity),
                        "in_review",
                        expected_version=1,
                        expected_current_revision=1,
                        selected_revision=1,
                        actor_id=actor().actor_id,
                        occurred_at=datetime.now(UTC),
                    )
                    await AuditStore().append(
                        session,
                        editorial_event(
                            change.event, revision, Provenance("legacy", "prepared"), actor=actor()
                        ),
                    )
        finally:
            await engine.dispose()

    asyncio.run(seed())

    async def snapshot() -> tuple[object, ...]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                stored = (
                    await connection.execute(
                        text("SELECT * FROM experimental_semantic_revisions WHERE resource_id=:id"),
                        {"id": identity.internal_id},
                    )
                ).one()
                events = (
                    await connection.execute(
                        text(
                            "SELECT * FROM experimental_semantic_audit_events "
                            "WHERE resource_id=:id "
                            "ORDER BY occurred_at,event_id"
                        ),
                        {"id": identity.internal_id},
                    )
                ).all()
                return tuple(stored), tuple(tuple(event) for event in events)
        finally:
            await engine.dispose()

    before = asyncio.run(snapshot())
    alembic("upgrade", "head")
    alembic("check")
    assert asyncio.run(snapshot()) == before

    async def verify_legacy() -> None:
        engine = create_async_engine(url)
        try:
            history = await HistoryService(async_sessionmaker(engine)).read_editorial(
                identity.internal_id, actor=actor()
            )
            assert history.state is None and len(history.events) == 3
            assert history.current == revision
        finally:
            await engine.dispose()

    asyncio.run(verify_legacy())
    alembic("downgrade", "0003_semantic_audit")
    assert asyncio.run(snapshot()) == before
    alembic("upgrade", "head")
    asyncio.run(verify_legacy())

    async def return_record() -> None:
        engine = create_async_engine(url)
        try:
            factory = PostgresEditorialTransactions(
                async_sessionmaker(engine),
                actor=actor(),
                provenance=Provenance("fixture", "editorial"),
            )
            service = EditorialService(factory)
            for version, target in ((1, "in_review"), (2, "draft")):
                await service.transition(
                    identity.internal_id,
                    target,
                    actor=actor(),
                    expected_version=version,
                    expected_current_revision=1,
                    selected_revision=1,
                    justification="Synthetic return" if target == "draft" else None,
                )
        finally:
            await engine.dispose()

    asyncio.run(return_record())
    final = asyncio.run(snapshot())
    alembic("downgrade", "0003_semantic_audit", succeeds=False)
    assert asyncio.run(snapshot()) == final
    alembic("check")

    # M06.02: exercise the actual head boundary with populated editorial data,
    # reusing this initially empty, exclusive migration database and its legacy r1.
    identities = asyncio.run(seed_stability_dataset(url))
    populated = asyncio.run(catalog_snapshot(url))
    alembic("upgrade", "head")
    assert asyncio.run(catalog_snapshot(url)) == populated
    for target in ("0004_semantic_editorial", "head"):
        alembic("downgrade" if target != "head" else "upgrade", target)
        assert asyncio.run(catalog_snapshot(url)) == populated
        asyncio.run(verify_stability_dataset(url, identities))
        assert asyncio.run(snapshot()) == final
    alembic("check")


async def catalog_snapshot(url: str) -> tuple[object, ...]:
    """Compare every persisted field, including null actors, not just row counts."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            snapshots: list[object] = []
            for statement in (
                "SELECT * FROM experimental_semantic_resources ORDER BY id",
                "SELECT * FROM experimental_semantic_revisions ORDER BY resource_id,number",
                "SELECT * FROM experimental_semantic_editorial_states ORDER BY resource_id",
                "SELECT * FROM experimental_semantic_audit_events "
                "ORDER BY resource_id,occurred_at,event_id",
                "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) "
                "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname LIKE 'experimental_semantic_%' "
                "ORDER BY c.relname,k.conname",
                "SELECT tablename,indexname,indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename LIKE 'experimental_semantic_%' "
                "ORDER BY tablename,indexname",
                "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) "
                "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname LIKE 'experimental_semantic_%' "
                "AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
            ):
                rows = (await connection.execute(text(statement))).all()
                snapshots.append(tuple(tuple(row) for row in rows))
            return tuple(snapshots)
    finally:
        await engine.dispose()


async def seed_stability_dataset(url: str) -> tuple[ResourceIdentity, ...]:
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine)
    service = BibliographicService(sessions)
    organization, subject, work, instance, item = (
        ResourceIdentity.create("https://m0602.example.invalid/resources") for _ in range(5)
    )
    provenance = Provenance("fixture:m0602", "test:stability", "Representative catalog")
    try:
        graphs = [Graph(), Graph(), graph_for(work), Graph(), Graph()]
        organization_graph, subject_graph, work_graph, instance_graph, item_graph = graphs
        organization_graph.add((URIRef(organization.uri), RDF.type, BF.Organization))
        organization_graph.add(
            (URIRef(organization.uri), RDFS.label, Literal("Library", lang="en"))
        )
        subject_graph.add((URIRef(subject.uri), RDF.type, SKOS.Concept))
        subject_graph.add((URIRef(subject.uri), SKOS.prefLabel, Literal("Catálogo", lang="pt")))
        subject_graph.add((URIRef(subject.uri), SKOS.inScheme, URIRef("urn:libris:test:scheme")))
        work_graph.add((URIRef(work.uri), BF.subject, URIRef(subject.uri)))
        work_graph.add((URIRef(work.uri), DCTERMS.source, URIRef("urn:libris:test:source")))
        instance_graph.add((URIRef(instance.uri), RDF.type, BF.Instance))
        instance_graph.add((URIRef(instance.uri), BF.instanceOf, URIRef(work.uri)))
        local = BNode()
        for triple in (
            (URIRef(item.uri), RDF.type, BF.Item),
            (URIRef(item.uri), BF.itemOf, URIRef(instance.uri)),
            (URIRef(item.uri), BF.heldBy, URIRef(organization.uri)),
            (URIRef(item.uri), BF.identifiedBy, local),
            (local, RDF.type, BF.Local),
            (local, RDF.value, Literal("M0602-001")),
        ):
            item_graph.add(triple)
        identities = (organization, subject, work, instance, item)
        for identity, graph in zip(identities, graphs, strict=True):
            await service.create(
                identity, graph, provenance, actor=actor(),
                profile=AUTHORITY_PROFILE_ID if identity in identities[:2]
                else INSTITUTIONAL_PROFILE_ID,
            )
        # Multiple record states coexist: draft, approved, published + candidate.
        await publish(sessions, work.internal_id)
        work_graph.add((URIRef(work.uri), DCTERMS.description, Literal("Candidate", lang="en")))
        await service.revise(work.internal_id, 1, work_graph, provenance, actor=actor())
        for target in (EditorialState.IN_REVIEW, EditorialState.APPROVED):
            await transition(sessions, work.internal_id, target, scope=EditorialScope.CANDIDATE)
            await transition(sessions, instance.internal_id, target)
        return identities
    finally:
        await engine.dispose()


async def verify_stability_dataset(url: str, identities: tuple[ResourceIdentity, ...]) -> None:
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine)
    service = BibliographicService(sessions)
    organization, subject, work, instance, item = identities
    try:
        current, candidate = await service.read(work.internal_id, actor=actor())
        public = await service.read_public(work.internal_id)
        assert public is not None and public[0].number == 1 and current.number == 2
        historical, original = await service.read(work.internal_id, 1, actor=actor())
        assert public[0] == historical and isomorphic(public[1], original)
        assert (URIRef(work.uri), DCTERMS.description, Literal("Candidate", lang="en")) in candidate
        assert not list(original.objects(URIRef(work.uri), DCTERMS.description))
        history = await state(sessions, work.internal_id)
        assert history.state is not None
        assert history.state.editorial_state == EditorialState.PUBLISHED
        assert history.state.candidate_state == EditorialState.APPROVED
        assert history.state.approved_revision == 2 and history.state.published_revision == 1
        assert [event.event_type for event in history.events] == [
            AuditEventType.CREATED, AuditEventType.SUBMITTED, AuditEventType.APPROVED,
            AuditEventType.PUBLISHED, AuditEventType.REVISED,
            AuditEventType.SUBMITTED, AuditEventType.APPROVED,
        ]
        assert (URIRef(work.uri), BF.subject, URIRef(subject.uri)) in candidate
        assert not list(candidate.triples((URIRef(subject.uri), None, None)))
        for identity in identities:
            revision, graph = await service.read(identity.internal_id, actor=actor())
            assert revision.identity == identity and revision.provenance.source == "fixture:m0602"
            assert revision.actor_id == actor().actor_id
            if identity == instance:
                assert (URIRef(instance.uri), BF.instanceOf, URIRef(work.uri)) in graph
                assert (await state(sessions, identity.internal_id)).state.editorial_state == (
                    EditorialState.APPROVED
                )
            if identity == item:
                assert (URIRef(item.uri), BF.itemOf, URIRef(instance.uri)) in graph
                assert (URIRef(item.uri), BF.heldBy, URIRef(organization.uri)) in graph
                assert Literal("M0602-001") in graph.objects(None, RDF.value)
            if identity != work:
                assert await service.read_public(identity.internal_id) is None
    finally:
        await engine.dispose()
