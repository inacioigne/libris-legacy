"""Diagnostics preserve CAS rollback on real PostgreSQL; never substitute SQLite."""

import asyncio
import os

import pytest
from libris.modules.bibliographic.application.diagnostics import (
    CatalogValidationRejected,
)
from libris.modules.bibliographic.application.service import BibliographicService
from libris.modules.bibliographic.domain.errors import ResourceNotFoundError
from libris.modules.bibliographic.domain.models import Provenance, ResourceIdentity
from libris.modules.bibliographic.infrastructure import validation
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.bibliographic.infrastructure.validation import (
    INSTITUTIONAL_PROFILE_ID,
    ShaclValidationError,
)
from libris.modules.identity.application.authorization import TechnicalContext
from libris.modules.identity.domain.authorization import Operation
from libris.modules.identity.domain.permissions import Permission as ContextPermission
from rdflib import Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import RDF
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


def test_public_rejections_rollback_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL descartável e migrado.")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("Integração exige PostgreSQL real via asyncpg.")
    asyncio.run(exercise(url, monkeypatch))


async def exercise(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(url)
    service = BibliographicService(async_sessionmaker(engine, expire_on_commit=False))
    identity = ResourceIdentity.create("https://catalog.example.invalid/resources")
    root = URIRef(identity.uri)
    graph = Graph()
    graph.add((root, RDF.type, BF.Work))
    title = URIRef(identity.uri + "#title")
    graph.add((root, BF.title, title))
    graph.add((title, RDF.type, BF.Title))
    graph.add((title, BF.mainTitle, Literal("Título sintético", lang="pt")))
    provenance = Provenance("fixture:synthetic", "pytest:diagnostics")
    invalid = Graph()
    invalid.add((root, RDF.type, BF.Work))
    try:
        with pytest.raises(CatalogValidationRejected):
            await service.create(
                identity,
                invalid,
                provenance,
                profile=INSTITUTIONAL_PROFILE_ID,
                context=TEST_CONTEXT,
            )
        with pytest.raises(ResourceNotFoundError):
            await service.read(identity.internal_id, context=TEST_CONTEXT)
        first = await service.create(
            identity, graph, provenance, profile=INSTITUTIONAL_PROFILE_ID, context=TEST_CONTEXT
        )
        with pytest.raises(CatalogValidationRejected) as rejection:
            await service.revise(identity.internal_id, 1, invalid, provenance, context=TEST_CONTEXT)
        assert rejection.value.result.conforms is False
        assert rejection.value.result.profile_release == INSTITUTIONAL_PROFILE_ID
        stored, restored = await service.read(identity.internal_id, context=TEST_CONTEXT)
        assert stored == first and isomorphic(graph, restored)
        with pytest.raises(ResourceNotFoundError):
            await service.read(identity.internal_id, 2, context=TEST_CONTEXT)

        def fail(*args: object, **kwargs: object) -> Graph:
            raise ShaclValidationError("engine_failure", "Falha técnica sintética.")

        with monkeypatch.context() as patch:
            patch.setattr(validation, "_load_shapes", fail)
            with pytest.raises(ShaclValidationError):
                await service.revise(
                    identity.internal_id, 1, graph, provenance, context=TEST_CONTEXT
                )
        stored, restored = await service.read(identity.internal_id, context=TEST_CONTEXT)
        assert stored == first and isomorphic(graph, restored)
        second = await service.revise(
            identity.internal_id, 1, graph, provenance, context=TEST_CONTEXT
        )
        assert second.number == 2
    finally:
        await engine.dispose()
