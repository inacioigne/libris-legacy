"""Real PostgreSQL HTTP tests; actor override isolates OIDC (no Keycloak claim)."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal as TypingLiteral
from uuid import UUID, uuid4

import httpx
import pytest
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.bibliographic.api.dependencies import get_catalog_service
from libris.modules.bibliographic.application.catalog import CatalogService
from libris.modules.bibliographic.application.lc_languages import (
    LC_LANGUAGES_VOCABULARY,
    LC_LANGUAGES_XML,
    LC_PROVIDER,
)
from libris.modules.bibliographic.application.vocabulary_catalog import (
    ImportedConcept,
    VocabularyCatalogService,
    VocabularyImport,
    VocabularyInput,
    VocabularyReleaseInput,
    VocabularySourceInput,
)
from libris.modules.bibliographic.domain.audit import AuditEvent
from libris.modules.bibliographic.domain.editorial import EditorialScope, EditorialState
from libris.modules.bibliographic.domain.models import Provenance, StaleRevisionError
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
    VocabularyRegistry,
)
from libris.modules.bibliographic.infrastructure.persistence import (
    SemanticRevision,
)
from libris.modules.bibliographic.infrastructure.serialization import graph_to_read
from libris.modules.bibliographic.infrastructure.vocabulary_catalog import (
    VocabularyIdentifierRecord,
    VocabularyImportRecord,
    VocabularyLabelRecord,
    VocabularyReleaseConceptRecord,
    VocabularyReleaseRecord,
)
from libris.modules.identity.api.dependencies import get_current_actor
from rdflib import Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_audit_postgres import actor
from test_editorial_postgres import publish, state, transition

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures/bibliographic/contracts"
COLLECTIONS = ["works", "instances", "items", "agents", "subjects"]
SINGULAR = dict(zip(COLLECTIONS, ["work", "instance", "item", "agent", "subject"], strict=True))


def payload(collection: str) -> dict[str, object]:
    return json.loads((FIXTURES / f"{SINGULAR[collection]}-create.json").read_text())


@asynccontextmanager
async def client_context() -> AsyncIterator[tuple[httpx.AsyncClient, CatalogService]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL descartável migrado para HEAD.")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("Integração exige PostgreSQL real via asyncpg.")
    app = create_app(Settings(_env_file=None, app_env="test", database_url=url))
    app.dependency_overrides[get_current_actor] = actor
    async with app.router.lifespan_context(app):
        service = CatalogService(app.state.database.sessions)
        app.dependency_overrides[get_catalog_service] = lambda: service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            yield client, service


async def create(client: httpx.AsyncClient, collection: str = "works") -> httpx.Response:
    response = await client.post(f"/api/v1/catalog/{collection}", json=payload(collection))
    assert response.status_code == 201, response.text
    return response


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_persistent_http_lifecycle(collection: str) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await create(client, collection)
            uri = first.headers["location"]
            initial = await client.get(uri)
            assert initial.status_code == 200 and initial.json() == first.json()
            assert initial.headers["etag"] == first.headers["etag"]
            identifier = UUID(first.json()["internal_id"])
            before = await state(service.sessions, identifier)
            field = (
                "variant_name"
                if collection == "agents"
                else ("alternative_label" if collection == "subjects" else "note")
            )
            values = (
                [{"value": "Replacement", "language": "pt"}]
                if collection in {"agents", "subjects"}
                else [{"label": [{"value": "Replacement", "language": "pt"}]}]
            )
            changed = await client.patch(
                uri, json={field: values}, headers={"If-Match": first.headers["etag"]}
            )
            assert changed.status_code == 200, changed.text
            assert changed.headers["etag"] != first.headers["etag"]
            assert all(changed.json()[field][0][key] == value for key, value in values[0].items())
            for name in ("internal_id", "uri"):
                assert changed.json()[name] == first.json()[name]
            current = await client.get(uri)
            assert current.json() == changed.json()
            assert current.headers["etag"] == changed.headers["etag"]
            after = await state(service.sessions, identifier)
            assert after.current.number == 2
            assert before.current.profile == after.current.profile
            assert len(after.events) == 2 and after.state.version == 2
            assert await service.read_public(identifier) is None
            # Wrong collection cannot return/update an object of another type.
            wrong = "subjects" if collection != "subjects" else "works"
            other = uri.replace(collection, wrong)
            assert (await client.get(other)).status_code == 404
            assert (
                await client.patch(other, json={}, headers={"If-Match": current.headers["etag"]})
            ).status_code == 404
            assert (await client.get(f"/api/v1/catalog/{collection}/{uuid4()}")).status_code == 404
            # Omission preserves all values, null clears optional lists.
            empty = await client.patch(uri, json={}, headers={"If-Match": current.headers["etag"]})
            assert empty.status_code == 200 and empty.json() == current.json()
            cleared = await client.patch(
                uri, json={field: None}, headers={"If-Match": empty.headers["etag"]}
            )
            assert cleared.status_code == 200 and cleared.json()[field] == []
            assert (
                await client.patch(
                    uri, json={field: []}, headers={"If-Match": cleared.headers["etag"]}
                )
            ).status_code == 200

    asyncio.run(scenario())


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_http_rejections_leave_persistence_unchanged(collection: str) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await create(client, collection)
            path, etag = first.headers["location"], first.headers["etag"]
            identifier = UUID(first.json()["internal_id"])
            before = await state(service.sessions, identifier)
            for field, value in {
                "internal_id": str(uuid4()),
                "uri": "https://forged.invalid/id",
                "profile": "fake",
                "actor_id": "forged",
                "revision": 9,
                "editorial_state": "published",
            }.items():
                response = await client.post(
                    f"/api/v1/catalog/{collection}",
                    json=payload(collection) | {field: value},
                )
                assert response.status_code == 422
                response = await client.patch(path, json={field: value}, headers={"If-Match": etag})
                assert response.status_code == 422
                assert response.headers["content-type"] == "application/problem+json"
            for value, status in [
                (None, 428),
                ('W/"weak"', 400),
                ("*", 400),
                ('"a", "b"', 400),
                ('"stale"', 412),
            ]:
                response = await client.patch(
                    path,
                    json={},
                    headers={"If-Match": value} if value is not None else {},
                )
                assert response.status_code == status, response.text
                assert response.json()["request_id"] == response.headers["x-request-id"]
            response = await client.patch(
                path, json={}, headers=[("If-Match", etag), ("If-Match", etag)]
            )
            assert response.status_code == 400
            assert await state(service.sessions, identifier) == before
            assert (await client.post(f"/api/v1/catalog/{collection}", json={})).status_code == 422

    asyncio.run(scenario())


def test_local_vocabulary_membership_gates_prevalidation_create_and_patch() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            now = datetime.now(UTC)
            vocabulary_uri = LC_LANGUAGES_VOCABULARY
            source_uri = LC_LANGUAGES_XML
            provider_uri = LC_PROVIDER
            catalog = VocabularyCatalogService(service.sessions)
            vocabulary = await catalog.register(
                VocabularySourceInput(
                    key="lc-languages-integration",
                    provider_uri=provider_uri,
                    source_uri=source_uri,
                    provenance={"test": "issue-71"},
                ),
                VocabularyInput(uri=vocabulary_uri, status="active"),
                now=now,
            )
            imported = ImportedConcept.model_validate(
                {
                    "concept": {
                        "kind": "controlled_concept",
                        "uri": f"{vocabulary_uri}eng",
                        "vocabulary": vocabulary_uri,
                        "label": [{"value": "English", "language": "en"}],
                        "notation": "eng",
                        "source": source_uri,
                        "status": "resolved",
                    }
                }
            )
            candidate = await catalog.import_release(
                vocabulary.id,
                VocabularyImport(
                    idempotency_key=f"issue71-{uuid4()}",
                    release=VocabularyReleaseInput(
                        release_key=f"issue71-{uuid4()}",
                        source_version="fixture-1",
                        source_uri=source_uri,
                        digest="f" * 64,
                        completeness="complete",
                        retrieved_at=now,
                    ),
                    concepts=(imported,),
                ),
                now=now,
            )
            async with service.sessions() as session:
                previous_active = await session.scalar(
                    select(VocabularyReleaseRecord).where(
                        VocabularyReleaseRecord.vocabulary_id == vocabulary.id,
                        VocabularyReleaseRecord.status == "active",
                    )
                )
            await catalog.activate_release(
                vocabulary.id,
                candidate.id,
                expected_active_release_id=previous_active.id if previous_active else None,
            )

            try:
                rejected_payload = payload("works")
                validation = await client.post(
                    "/api/v1/catalog/works/validation",
                    json={
                        "kind": "dto",
                        "profile_release": CONTROLLED_VOCABULARY_PROFILE_ID,
                        "resource": rejected_payload,
                    },
                )
                assert validation.status_code == 200, validation.text
                assert validation.json()["conforms"] is False
                diagnostic = next(
                    issue
                    for issue in validation.json()["issues"]
                    if issue["code"] == "reference.vocabulary_mismatch"
                )
                assert diagnostic["field_ids"] == ["/language"]
                assert diagnostic["path"]["iri"] == (
                    "http://id.loc.gov/ontologies/bibframe/language"
                )

                rejected = await client.post("/api/v1/catalog/works", json=rejected_payload)
                assert rejected.status_code == 422, rejected.text
                assert rejected.json()["validation"]["issues"][0]["code"] == (
                    "reference.vocabulary_mismatch"
                )

                accepted_payload = payload("works")
                accepted_payload["language"] = [{"uri": f"{vocabulary_uri}eng"}]
                created = await client.post("/api/v1/catalog/works", json=accepted_payload)
                assert created.status_code == 201, created.text
                identifier = UUID(created.json()["internal_id"])
                before = await state(service.sessions, identifier)
                rejected_patch = await client.patch(
                    created.headers["location"],
                    json={"language": [{"uri": f"{vocabulary_uri}por"}]},
                    headers={"If-Match": created.headers["etag"]},
                )
                assert rejected_patch.status_code == 422, rejected_patch.text
                after = await state(service.sessions, identifier)
                assert after.current.number == before.current.number == 1
                assert after.current.profile == CONTROLLED_VOCABULARY_PROFILE_ID
                current = await client.get(created.headers["location"])
                assert current.headers["etag"] == created.headers["etag"]
            finally:
                if previous_active is not None:
                    await catalog.activate_release(
                        vocabulary.id,
                        previous_active.id,
                        expected_active_release_id=candidate.id,
                    )
                else:
                    async with service.sessions() as session, session.begin():
                        active_release = await session.get(
                            VocabularyReleaseRecord, candidate.id
                        )
                        if active_release is not None:
                            active_release.status = "superseded"
                async with service.sessions() as session, session.begin():
                    occurrence_ids = select(VocabularyReleaseConceptRecord.id).where(
                        VocabularyReleaseConceptRecord.release_id == candidate.id
                    )
                    await session.execute(
                        delete(VocabularyLabelRecord).where(
                            VocabularyLabelRecord.release_concept_id.in_(occurrence_ids)
                        )
                    )
                    await session.execute(
                        delete(VocabularyIdentifierRecord).where(
                            VocabularyIdentifierRecord.release_concept_id.in_(occurrence_ids)
                        )
                    )
                    await session.execute(
                        delete(VocabularyReleaseConceptRecord).where(
                            VocabularyReleaseConceptRecord.release_id == candidate.id
                        )
                    )
                    await session.execute(
                        delete(VocabularyImportRecord).where(
                            VocabularyImportRecord.release_id == candidate.id
                        )
                    )
                    release = await session.get(VocabularyReleaseRecord, candidate.id)
                    if release is not None:
                        await session.delete(release)

    asyncio.run(scenario())


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_two_http_sessions_race_through_persistent_cas(collection: str) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await create(client, collection)
            path = first.headers["location"]
            identifier = UUID(first.json()["internal_id"])
            both_read = asyncio.Event()
            entered = 0
            original = service.update_resource

            async def synchronized(*args: object, **kwargs: object) -> object:
                nonlocal entered
                entered += 1
                if entered == 2:
                    both_read.set()
                await asyncio.wait_for(both_read.wait(), 10)
                return await original(*args, **kwargs)

            service.update_resource = synchronized
            # Each request opens its own sessions/transaction, both with the same view.
            field = (
                "variant_name" if collection == "agents"
                else ("alternative_label" if collection == "subjects" else "note")
            )
            responses = await asyncio.gather(
                *[
                    client.patch(
                        path,
                        json={field: [{"value": value}] if collection in {"agents", "subjects"}
                              else [{"label": [{"value": value}]}]},
                        headers={"If-Match": first.headers["etag"]},
                    )
                    for value in ("winner-a", "winner-b")
                ]
            )
            assert sorted(r.status_code for r in responses) == [200, 412]
            winner = next(r for r in responses if r.status_code == 200)
            assert (await client.get(path)).json() == winner.json()
            after = await state(service.sessions, identifier)
            assert after.current.number == 2 and after.state.version == 2
            assert len(after.events) == 2
            async with service.sessions() as session:
                revisions = (
                    await session.scalars(
                        select(SemanticRevision).where(SemanticRevision.resource_id == identifier)
                    )
                ).all()
                assert len(revisions) == 2
            assert (
                await client.patch(path, json={}, headers={"If-Match": first.headers["etag"]})
            ).status_code == 412

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["post", "patch"])
@pytest.mark.parametrize("failure", ["snapshot", "audit", "integrity", "cas", "commit"])
def test_http_transaction_failures_roll_back(
    operation: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await create(client)
            identifier = UUID(first.json()["internal_id"])
            before = await state(service.sessions, identifier)
            async with service.sessions() as session:
                counts_before = tuple(
                    [
                        (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                        for table in [
                            "experimental_semantic_resources",
                            "experimental_semantic_revisions",
                            "experimental_semantic_editorial_states",
                            "experimental_semantic_audit_events",
                        ]
                    ]
                )
            save = service.store.save
            append = service.audit.append

            async def fail_save(*args: object, **kwargs: object) -> None:
                await save(*args, **kwargs)
                raise RuntimeError("Synthetic snapshot failure after insert")

            async def fail_audit(session: AsyncSession, event: AuditEvent) -> None:
                if failure == "integrity":
                    event = replace(event, revision_number=999999)
                await append(session, event)
                if failure == "cas":
                    raise StaleRevisionError("Synthetic late CAS failure")
                if failure == "audit":
                    raise RuntimeError("Synthetic post-append failure")
                if failure == "commit":
                    # PostgreSQL deferred FK checked only at COMMIT, after all writes.
                    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                    await session.execute(
                        text(
                            "UPDATE experimental_semantic_resources SET current_revision=999999 "
                            "WHERE id=:id"
                        ),
                        {"id": identifier},
                    )

            with monkeypatch.context() as scoped:
                if failure == "snapshot":
                    scoped.setattr(service.store, "save", fail_save)
                else:
                    scoped.setattr(service.audit, "append", fail_audit)
                response = (
                    await client.post("/api/v1/catalog/works", json=payload("works"))
                    if (operation == "post")
                    else await client.patch(
                        first.headers["location"],
                        json={},
                        headers={"If-Match": first.headers["etag"]},
                    )
                )
                assert response.status_code in {409, 412, 500}, response.text
            assert await state(service.sessions, identifier) == before
            async with service.sessions() as session:
                counts_after = tuple(
                    [
                        (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                        for table in [
                            "experimental_semantic_resources",
                            "experimental_semantic_revisions",
                            "experimental_semantic_editorial_states",
                            "experimental_semantic_audit_events",
                        ]
                    ]
                )
            assert counts_after == counts_before

    asyncio.run(scenario())


def test_patch_extensions_validation_and_public_snapshot() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await create(client)
            identifier = UUID(first.json()["internal_id"])
            revision, graph = await service.read(identifier, actor=actor())
            extension = (
                URIRef(revision.identity.uri),
                DCTERMS.abstract,
                Literal("Preserve", lang="pt"),
            )
            graph.add(extension)
            await service.revise(identifier, 1, graph, Provenance("test", "seed"), actor=actor())
            # Strip references to simplify publication validation without remote classification.
            from libris.modules.bibliographic.api.schemas import WorkUpdate
            from libris.modules.bibliographic.infrastructure.serialization import (
                apply_update,
            )

            revision, graph = await service.read(identifier, actor=actor())
            graph = apply_update(
                graph,
                WorkUpdate(contribution=[], subject=[]),
                revision.identity,
                profile=revision.profile,
            )
            await service.revise(identifier, 2, graph, Provenance("test", "seed"), actor=actor())
            await publish(service.sessions, identifier)
            selected = await service.read_public(identifier)
            assert selected is not None and selected[0].number == 3
            current = await client.get(first.headers["location"])
            patched = await client.patch(
                first.headers["location"],
                json={"title": [{"main_title": [{"value": "Candidate"}]}]},
                headers={"If-Match": current.headers["etag"]},
            )
            assert patched.status_code == 200, patched.text
            latest, complete = await service.read(identifier, actor=actor())
            assert latest.number == 4 and extension in complete
            public = await service.read_public(identifier)
            assert public[0] == selected[0]
            assert isomorphic(public[1], selected[1])
            assert (
                graph_to_read(public[1], public[0].identity).model_dump(mode="json")["title"]
                != patched.json()["title"]
            )
            await transition(
                service.sessions,
                identifier,
                EditorialState.IN_REVIEW,
                scope=EditorialScope.CANDIDATE,
            )
            # Editorial-only transition changes ETag even though content is unchanged.
            stale = await client.patch(
                first.headers["location"],
                json={},
                headers={"If-Match": patched.headers["etag"]},
            )
            assert stale.status_code == 412
            current = await client.get(first.headers["location"])
            assert (
                await client.patch(
                    first.headers["location"],
                    json={},
                    headers={"If-Match": current.headers["etag"]},
                )
            ).status_code == 409
            assert await service.read_public(identifier) is not None
            subject = await create(client, "subjects")
            before = await state(service.sessions, UUID(subject.json()["internal_id"]))
            response = await client.patch(
                subject.headers["location"],
                json={"alternative_label": subject.json()["preferred_label"]},
                headers={"If-Match": subject.headers["etag"]},
            )
            assert response.status_code == 422 and response.json()["code"] == "schema.invalid"
            assert await state(service.sessions, UUID(subject.json()["internal_id"])) == before

    asyncio.run(scenario())


def test_local_references_resolved_in_the_write_transaction() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            work = await create(client)
            identifier = UUID(work.json()["internal_id"])
            local = {
                "kind": "local",
                "entity_type": "Work",
                "internal_id": str(identifier),
            }
            instance = await client.post("/api/v1/catalog/instances", json={"instance_of": local})
            assert instance.status_code == 201, instance.text
            assert instance.json()["instance_of"] == {"uri": work.json()["uri"]}
            for bad in [
                local | {"uri": "https://wrong.invalid/stored"},
                local | {"internal_id": str(uuid4())},
            ]:
                response = await client.post("/api/v1/catalog/instances", json={"instance_of": bad})
                assert response.status_code == 422
                assert response.json()["code"] == "reference.invalid"
            # A stored subject cannot be used as a Work even in the URI-only form.
            subject = await create(client, "subjects")
            response = await client.post(
                "/api/v1/catalog/instances",
                json={"instance_of": {"uri": subject.json()["uri"]}},
            )
            assert response.status_code == 400
            agent = await create(client, "agents")  # Person, never Organization
            response = await client.post(
                "/api/v1/catalog/items",
                json={
                    "item_of": {"uri": instance.json()["uri"]},
                    "identified_by": [{"kind": "Local", "value": "synthetic"}],
                    "held_by": [{"uri": agent.json()["uri"]}],
                },
            )
            assert response.status_code == 400
            assert (await state(service.sessions, identifier)).current.number == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["shacl", "database_cas"])
def test_post_advance_rejection_is_atomic(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await create(client)
            identifier = UUID(first.json()["internal_id"])
            before = await state(service.sessions, identifier)
            advance = service.store.advance
            from libris.modules.bibliographic.application.service import (
                prepare_document,
            )
            from libris.modules.bibliographic.infrastructure.rdf import BF

            async def wrong_expectation(
                session: AsyncSession, identifier: UUID, expected: int
            ) -> str:
                return await advance(session, identifier, expected + 1)

            def invalid_complete_graph(
                graph: Graph,
                uri: str,
                profile: str,
                *,
                vocabularies: VocabularyRegistry | None = None,
                operation: TypingLiteral[
                    "prevalidation", "create", "update", "restore", "publish"
                ] = "create",
                previous_graph: Graph | None = None,
            ) -> list[dict[str, object]]:
                graph.remove((URIRef(uri), BF.title, None))
                return prepare_document(
                    graph,
                    uri,
                    profile,
                    vocabularies=vocabularies,
                    operation=operation,
                    previous_graph=previous_graph,
                )

            with monkeypatch.context() as scoped:
                if failure == "database_cas":
                    scoped.setattr(service.store, "advance", wrong_expectation)
                else:
                    scoped.setattr(
                        "libris.modules.bibliographic.application.service.prepare_document",
                        invalid_complete_graph,
                    )
                response = await client.patch(
                    first.headers["location"],
                    json={},
                    headers={"If-Match": first.headers["etag"]},
                )
            assert response.status_code == (412 if failure == "database_cas" else 422), (
                response.text
            )
            assert await state(service.sessions, identifier) == before
            async with service.sessions() as session:
                assert (
                    len(
                        (
                            await session.scalars(
                                select(SemanticRevision).where(
                                    SemanticRevision.resource_id == identifier
                                )
                            )
                        ).all()
                    )
                    == 1
                )
            assert (await client.get(first.headers["location"])).headers["etag"] == first.headers[
                "etag"
            ]

    asyncio.run(scenario())


def test_legacy_read_does_not_initialize_editorial_state() -> None:
    async def scenario() -> None:
        from datetime import UTC, datetime

        from libris.modules.bibliographic.application.service import prepare_document
        from libris.modules.bibliographic.domain.models import (
            ResourceIdentity,
            Revision,
        )
        from libris.modules.bibliographic.infrastructure.editorial import EditorialStore
        from libris.modules.bibliographic.infrastructure.validation import (
            INSTITUTIONAL_PROFILE_ID,
        )
        from test_audit_postgres import graph_for

        async with client_context() as (client, service):
            identity = ResourceIdentity.create("https://historical.invalid/records")
            revision = Revision(
                identity,
                1,
                datetime.now(UTC),
                Provenance("legacy", "seed"),
                INSTITUTIONAL_PROFILE_ID,
            )
            graph = graph_for(identity)
            async with service.sessions() as session, session.begin():
                await service.store.create(session, identity)
                await service.store.save(
                    session,
                    revision,
                    prepare_document(graph, identity.uri, INSTITUTIONAL_PROFILE_ID),
                )
            path = f"/api/v1/catalog/works/{identity.internal_id}"
            read = await client.get(path)
            assert read.status_code == 200 and read.json()["uri"] == identity.uri
            async with service.sessions() as session:
                assert await EditorialStore().get(session, identity.internal_id) is None
            assert await service.read_public(identity.internal_id) is None
            patched = await client.patch(path, json={}, headers={"If-Match": read.headers["etag"]})
            assert patched.status_code == 200
            after = await state(service.sessions, identity.internal_id)
            assert after.state.version == 2 and after.current.number == 2
            assert len(after.events) == 1 and after.current.actor_id == actor().actor_id
            assert (await service.read(identity.internal_id, 1, actor=actor()))[0].actor_id is None

    asyncio.run(scenario())


def test_cross_references_in_parallel_do_not_deadlock() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first, second = await create(client), await create(client)
            responses = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        client.patch(
                            source.headers["location"],
                            json={
                                "related_to": [
                                    {
                                        "kind": "local",
                                        "entity_type": "Work",
                                        "internal_id": target.json()["internal_id"],
                                    }
                                ]
                            },
                            headers={"If-Match": source.headers["etag"]},
                        )
                        for source, target in [(first, second), (second, first)]
                    ]
                ),
                timeout=10,
            )
            assert [response.status_code for response in responses] == [200, 200]

    asyncio.run(scenario())
