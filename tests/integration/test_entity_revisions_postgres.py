"""Independent bibliographic entities on real, dedicated PostgreSQL."""

import asyncio
import os
from pathlib import Path

import pytest
from libris.modules.bibliographic.application.service import BibliographicService
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    StaleRevisionError,
)
from libris.modules.bibliographic.infrastructure.ownership import (
    ENTITY_TYPES,
    compose_snapshots,
    owned_nodes,
)
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    new_graph,
    parse_document,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    validate_profile,
)
from libris.modules.identity.application.authorization import TechnicalContext
from libris.modules.identity.domain.authorization import Operation
from libris.modules.identity.domain.permissions import Permission as ContextPermission
from rdflib import Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import RDF, RDFS
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


def test_entity_revisions_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL descartável e migrado.")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("Integração exige PostgreSQL real via asyncpg.")
    asyncio.run(exercise_entities(url, monkeypatch))


async def exercise_entities(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(__file__).parents[1] / "fixtures/bibliographic/institutional/multiple-items.ttl"
    source = parse_document(path.read_text(), "turtle")
    roots = {root for kind in ENTITY_TYPES for root in source.subjects(RDF.type, kind)}
    identities = {
        root: ResourceIdentity.create("https://catalog.example.invalid/resources") for root in roots
    }
    graph = new_graph()
    for subject, predicate, value in source:
        graph.add(
            (
                URIRef(identities[subject].uri) if subject in identities else subject,
                predicate,
                URIRef(identities[value].uri) if value in identities else value,
            )
        )
    snapshots: dict[URIRef, Graph] = {}
    by_uri = {URIRef(identity.uri): identity for identity in identities.values()}
    for root in by_uri:
        owned = owned_nodes(graph, root)
        snapshot = new_graph()
        for subject, predicate, value in graph:
            if subject in owned:
                snapshot.add((subject, predicate, value))
        snapshots[root] = snapshot
    engine = create_async_engine(url)
    service = BibliographicService(async_sessionmaker(engine, expire_on_commit=False))
    provenance = Provenance("fixture:synthetic", "pytest:stage3")
    try:
        for root, snapshot in snapshots.items():
            profile = (
                INSTITUTIONAL_PROFILE_ID
                if any(
                    (root, RDF.type, kind) in snapshot for kind in (BF.Work, BF.Instance, BF.Item)
                )
                else AUTHORITY_PROFILE_ID
            )
            first = await service.create(
                by_uri[root], snapshot, provenance, profile=profile, context=TEST_CONTEXT
            )
            assert first.number == 1
            assert first.profile == profile
            stored, recovered = await service.read(by_uri[root].internal_id, context=TEST_CONTEXT)
            assert first == stored
            assert isomorphic(snapshot, recovered)
        work = next(graph.subjects(RDF.type, BF.Work))
        instance = next(graph.subjects(RDF.type, BF.Instance))
        person = next(graph.subjects(RDF.type, BF.Person))
        assert (
            isinstance(work, URIRef) and isinstance(instance, URIRef) and isinstance(person, URIRef)
        )
        before_work, original_work = await service.read(
            by_uri[work].internal_id, context=TEST_CONTEXT
        )
        monkeypatch.setenv("RESOURCE_BASE_URI", "https://changed.example.invalid/resources")
        person_graph = snapshots[person]
        person_graph.set((person, RDFS.label, Literal("Autoridade revisada", lang="pt")))
        authority_revision = await service.revise(
            by_uri[person].internal_id, 1, person_graph, provenance, context=TEST_CONTEXT
        )
        assert authority_revision.number == 2
        assert authority_revision.identity == by_uri[person]
        after_work, current_work = await service.read(
            by_uri[work].internal_id, context=TEST_CONTEXT
        )
        assert after_work == before_work
        assert isomorphic(current_work, original_work)
        instance_graph = snapshots[instance]
        instance_graph.set(
            (instance, BF.editionStatement, Literal("Metadado corrigido", lang="pt"))
        )
        second_instance = await service.revise(
            by_uri[instance].internal_id, 1, instance_graph, provenance, context=TEST_CONTEXT
        )
        assert second_instance.number == 2
        assert (await service.read(by_uri[work].internal_id, context=TEST_CONTEXT))[
            0
        ] == before_work
        for item in graph.subjects(RDF.type, BF.Item):
            assert (await service.read(by_uri[URIRef(item)].internal_id, context=TEST_CONTEXT))[
                0
            ].number == 1
        item_root = next(graph.subjects(RDF.type, BF.Item))
        assert isinstance(item_root, URIRef)
        item_graph = snapshots[item_root]
        item_graph.add((item_root, BF.custodialHistory, Literal("Doação corrigida", lang="pt")))
        item_revision = await service.revise(
            by_uri[item_root].internal_id, 1, item_graph, provenance, context=TEST_CONTEXT
        )
        assert item_revision.number == 2
        assert (await service.read(by_uri[work].internal_id, context=TEST_CONTEXT))[
            0
        ] == before_work
        assert (await service.read(by_uri[instance].internal_id, context=TEST_CONTEXT))[
            0
        ] == second_instance
        invalid = new_graph()
        invalid += instance_graph
        invalid.add((instance, BF.instanceOf, URIRef("urn:test:another-work")))
        with pytest.raises(ValueError):
            await service.revise(
                by_uri[instance].internal_id, 2, invalid, provenance, context=TEST_CONTEXT
            )
        assert (await service.read(by_uri[instance].internal_id, context=TEST_CONTEXT))[
            0
        ] == second_instance
        # A structurally valid graph of another kind still cannot change the identity's kind.
        changed_kind = new_graph()
        changed_kind.add((instance, RDF.type, BF.Work))
        title = URIRef(f"{instance}#title")
        changed_kind.add((instance, BF.title, title))
        changed_kind.add((title, RDF.type, BF.Title))
        changed_kind.add((title, BF.mainTitle, Literal("Novo tipo inválido", lang="pt")))
        with pytest.raises(ValueError, match="tipo da identidade"):
            await service.revise(
                by_uri[instance].internal_id, 2, changed_kind, provenance, context=TEST_CONTEXT
            )
        assert (await service.read(by_uri[instance].internal_id, context=TEST_CONTEXT))[
            0
        ] == second_instance
        historical, original_person = await service.read(
            by_uri[person].internal_id, 1, context=TEST_CONTEXT
        )
        assert historical.number == 1
        assert Literal("Autoridade fictícia 10", lang="pt") in original_person.objects()
        assert Literal("Autoridade revisada", lang="pt") not in original_person.objects()
        results = await asyncio.gather(
            service.revise(
                by_uri[work].internal_id, 1, current_work, provenance, context=TEST_CONTEXT
            ),
            service.revise(
                by_uri[work].internal_id, 1, current_work, provenance, context=TEST_CONTEXT
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, StaleRevisionError) for result in results) == 1
        assert sum(not isinstance(result, Exception) for result in results) == 1
        restored: list[tuple[URIRef, Graph]] = []
        for root, identity in by_uri.items():
            revision, recovered = await service.read(identity.internal_id, context=TEST_CONTEXT)
            assert revision.identity == identity
            restored.append((root, recovered))
        composed = compose_snapshots(restored)
        assert validate_profile(composed, INSTITUTIONAL_PROFILE_ID, composed=True).conforms
        assert len(set(composed.subjects(RDF.type, BF.Person))) == 1
        for item in composed.subjects(RDF.type, BF.Item):
            assert composed.value(item, BF.itemOf) == instance
        assert composed.value(instance, BF.instanceOf) == work
    finally:
        await engine.dispose()
