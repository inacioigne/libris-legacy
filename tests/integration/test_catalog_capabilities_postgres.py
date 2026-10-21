"""M05.04: CAS, rollback, publicação e ausência de DML em PostgreSQL exclusivo."""

import asyncio
from uuid import UUID, uuid4

import pytest
from libris.modules.bibliographic.api.capabilities import get_relationship_service
from libris.modules.bibliographic.application.catalog_relationships import (
    RelationshipService,
)
from libris.modules.bibliographic.domain.editorial import EditorialScope, EditorialState
from libris.modules.bibliographic.domain.models import Provenance
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.bibliographic.infrastructure.validation import (
    INSTITUTIONAL_PROFILE_ID,
)
from rdflib import Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS
from sqlalchemy import event, text
from test_audit_postgres import actor
from test_catalog_http_postgres import client_context, create
from test_editorial_postgres import publish, state, transition

pytestmark = pytest.mark.integration


def relationship(
    source: dict[str, object],
    target: dict[str, object],
    predicate: str = str(BF.relatedTo),
    entity: str = "Work",
) -> dict[str, object]:
    return {
        "kind": "direct",
        "source": {
            "kind": "local",
            "entity_type": entity,
            "internal_id": source["internal_id"],
        },
        "target": {
            "kind": "local",
            "entity_type": entity,
            "internal_id": target["internal_id"],
        },
        "predicate": predicate,
    }


async def patch_links(client: object, path: str, operations: list[dict[str, object]]) -> object:
    current = await client.get(path)
    assert current.status_code == 200, current.text
    return await client.patch(
        path,
        json={"operations": operations},
        headers={"If-Match": current.headers["etag"]},
    )


def test_relationship_lifecycle_integrity_history_and_extensions() -> None:
    async def scenario() -> None:
        async with client_context() as (client, catalog):
            source, target, replacement = (
                await create(client),
                await create(client),
                await create(client),
            )
            identifier = UUID(source.json()["internal_id"])
            revision, graph = await catalog.read(identifier, actor=actor())
            extension = (
                URIRef(revision.identity.uri),
                DCTERMS.abstract,
                Literal("Preservar", lang="pt-BR"),
            )
            graph.add(extension)
            await catalog.revise(
                identifier, 1, graph, Provenance("test", "extension"), actor=actor()
            )
            before = await state(catalog.sessions, identifier)
            old_revision, old_graph = await catalog.read(identifier, 2, actor=actor())
            path = source.headers["location"] + "/relationships"
            link = relationship(source.json(), target.json())
            added = await patch_links(client, path, [{"op": "add", "links": [link]}])
            assert added.status_code == 200, added.text
            assert extension in (await catalog.read(identifier, actor=actor()))[1]
            duplicate_before = await state(catalog.sessions, identifier)
            duplicate = await patch_links(client, path, [{"op": "add", "links": [link]}])
            assert duplicate.status_code == 422, duplicate.text
            assert await state(catalog.sessions, identifier) == duplicate_before
            new_link = relationship(source.json(), replacement.json())
            replaced = await patch_links(
                client,
                path,
                [
                    {
                        "op": "replace",
                        "predicate": str(BF.relatedTo),
                        "links": [new_link],
                    }
                ],
            )
            assert replaced.status_code == 200, replaced.text
            removed = await patch_links(client, path, [{"op": "remove", "links": [new_link]}])
            assert removed.status_code == 200
            missing = await patch_links(client, path, [{"op": "remove", "links": [new_link]}])
            assert missing.status_code == 422
            assert (await catalog.read(identifier, 2, actor=actor()))[0] == old_revision
            assert isomorphic((await catalog.read(identifier, 2, actor=actor()))[1], old_graph)
            after = await state(catalog.sessions, identifier)
            assert after.current.number == before.current.number + 3
            assert len(after.events) == len(before.events) + 3
            assert after.state.published_revision is None
            assert (
                await client.get(f"/api/v1/catalog/works/{uuid4()}/relationships")
            ).status_code == 404
            bad = relationship(source.json(), {"internal_id": str(uuid4())})
            assert (
                await patch_links(client, path, [{"op": "add", "links": [bad]}])
            ).status_code == 422
            # Explicit external IRI remains external and never creates a local entity.
            external = dict(
                link,
                target={
                    "kind": "external",
                    "entity_type": "Work",
                    "uri": "urn:external:work",
                },
            )
            result = await patch_links(client, path, [{"op": "add", "links": [external]}])
            assert result.status_code == 200, result.text
            found = [
                item for item in result.json()["links"] if item["predicate"] == str(BF.relatedTo)
            ]
            assert found[0]["target"]["kind"] == "external"
            pending = dict(
                link,
                target={
                    "kind": "unreconciled",
                    "entity_type": "Work",
                    "namespace": "urn:import:test",
                    "key": "w1",
                    "label": "Pending",
                },
            )
            unchanged = await state(catalog.sessions, identifier)
            assert (
                await patch_links(client, path, [{"op": "add", "links": [pending]}])
            ).status_code == 422
            assert await state(catalog.sessions, identifier) == unchanged

    asyncio.run(scenario())


def test_inverses_cycles_and_target_types() -> None:
    async def scenario() -> None:
        async with client_context() as (client, catalog):
            work = await create(client)
            instance = await client.post(
                "/api/v1/catalog/instances",
                json={"instance_of": {"uri": work.json()["uri"]}},
            )
            assert instance.status_code == 201
            path = work.headers["location"] + "/relationships"
            inverses = await client.get(path, params={"include_inverses": True})
            assert inverses.status_code == 200, inverses.text
            assert any(
                link["predicate"] == str(BF.hasInstance) for link in inverses.json()["links"]
            )
            identifier = UUID(work.json()["internal_id"])
            assert not list(
                (await catalog.read(identifier, actor=actor()))[1].objects(
                    URIRef(work.json()["uri"]), BF.hasInstance
                )
            )
            inverse = relationship(work.json(), instance.json(), str(BF.hasInstance))
            inverse["target"]["entity_type"] = "Instance"
            assert (
                await patch_links(client, path, [{"op": "add", "links": [inverse]}])
            ).status_code == 422
            subject = await create(client, "subjects")
            wrong = relationship(work.json(), subject.json())
            assert (
                await patch_links(client, path, [{"op": "add", "links": [wrong]}])
            ).status_code == 422
            second = await create(client)
            link = relationship(work.json(), second.json(), str(BF.translationOf))
            assert (
                await patch_links(client, path, [{"op": "add", "links": [link]}])
            ).status_code == 200
            opposite = relationship(second.json(), work.json(), str(BF.translationOf))
            assert (
                await patch_links(
                    client,
                    second.headers["location"] + "/relationships",
                    [{"op": "add", "links": [opposite]}],
                )
            ).status_code == 422
            self_link = relationship(work.json(), work.json())
            assert (
                await patch_links(client, path, [{"op": "add", "links": [self_link]}])
            ).status_code == 422

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["snapshot", "audit", "cas", "commit"])
def test_relationship_failure_rolls_back(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with client_context() as (client, catalog):
            source, target = await create(client), await create(client)
            service = RelationshipService(catalog.sessions)
            client._transport.app.dependency_overrides[get_relationship_service] = lambda: service
            path = source.headers["location"] + "/relationships"
            identifier = UUID(source.json()["internal_id"])
            before = await state(catalog.sessions, identifier)
            append, save, advance = (
                service.audit.append,
                service.store.save,
                service.store.advance,
            )

            async def failed_audit(session: object, audit: object) -> None:
                await append(session, audit)
                if failure == "commit":
                    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                    await session.execute(
                        text(
                            "UPDATE experimental_semantic_resources SET current_revision=999999 "
                            "WHERE id=:id"
                        ),
                        {"id": identifier},
                    )
                else:
                    raise RuntimeError("synthetic post-append failure")

            async def failed_save(*args: object, **kwargs: object) -> None:
                await save(*args, **kwargs)
                raise RuntimeError("synthetic post-snapshot failure")

            async def failed_cas(session: object, id: UUID, expected: int) -> str:
                return await advance(session, id, expected + 1)

            if failure == "snapshot":
                monkeypatch.setattr(service.store, "save", failed_save)
            elif failure == "cas":
                monkeypatch.setattr(service.store, "advance", failed_cas)
            else:
                monkeypatch.setattr(service.audit, "append", failed_audit)
            response = await patch_links(
                client,
                path,
                [{"op": "add", "links": [relationship(source.json(), target.json())]}],
            )
            assert response.status_code in {409, 412, 500}, response.text
            assert await state(catalog.sessions, identifier) == before

    asyncio.run(scenario())


def test_relationship_concurrent_cas_and_editorial_publication() -> None:
    async def scenario() -> None:
        async with client_context() as (client, catalog):
            # Minimal Work without external relationships can be published internally.
            source = await client.post(
                "/api/v1/catalog/works",
                json={"title": [{"main_title": [{"value": "Publish"}]}]},
            )
            target = await create(client)
            identifier = UUID(source.json()["internal_id"])
            await publish(catalog.sessions, identifier)
            public = await catalog.read_public(identifier)
            service = RelationshipService(catalog.sessions)
            client._transport.app.dependency_overrides[get_relationship_service] = lambda: service
            original = service.update_relationships
            entered, barrier = 0, asyncio.Event()

            async def race(*args: object, **kwargs: object) -> object:
                nonlocal entered
                entered += 1
                if entered == 2:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), 10)
                return await original(*args, **kwargs)

            service.update_relationships = race
            path = source.headers["location"] + "/relationships"
            current = await client.get(path)
            responses = await asyncio.gather(
                *[
                    client.patch(
                        path,
                        json={
                            "operations": [
                                {
                                    "op": "add",
                                    "links": [relationship(source.json(), target.json())],
                                }
                            ]
                        },
                        headers={"If-Match": current.headers["etag"]},
                    )
                    for _ in range(2)
                ]
            )
            assert sorted(r.status_code for r in responses) == [200, 412]
            service.update_relationships = original
            assert (await catalog.read_public(identifier))[0] == public[0]
            assert isomorphic((await catalog.read_public(identifier))[1], public[1])
            current = await client.get(path)
            await transition(
                catalog.sessions,
                identifier,
                EditorialState.IN_REVIEW,
                scope=EditorialScope.CANDIDATE,
            )
            assert (
                await client.patch(
                    path,
                    json={"operations": []},
                    headers={"If-Match": current.headers["etag"]},
                )
            ).status_code == 412
            current = await client.get(path)
            assert (
                await client.patch(
                    path,
                    json={"operations": []},
                    headers={"If-Match": current.headers["etag"]},
                )
            ).status_code == 409

    asyncio.run(scenario())


def test_prevalidation_observably_has_no_dml_and_matches_write() -> None:
    async def scenario() -> None:
        async with client_context() as (client, catalog):
            source = await create(client)
            identifier = UUID(source.json()["internal_id"])
            before = await state(catalog.sessions, identifier)
            statements: list[str] = []
            engine = catalog.sessions.kw["bind"].sync_engine

            def observe(
                connection: object,
                cursor: object,
                statement: str,
                parameters: object,
                context: object,
                many: bool,
            ) -> None:
                statements.append(statement.strip().upper())

            event.listen(engine, "before_cursor_execute", observe)
            try:
                for body in [
                    {
                        "kind": "dto",
                        "profile_release": INSTITUTIONAL_PROFILE_ID,
                        "resource": {"title": [{"main_title": [{"value": "Preview"}]}]},
                    },
                    {"kind": "revision", "resource_id": str(identifier)},
                    {
                        "kind": "dto",
                        "profile_release": INSTITUTIONAL_PROFILE_ID,
                        "resource": {
                            "title": [{"main_title": [{"value": "Linked"}]}],
                            "related_to": [
                                {
                                    "kind": "local",
                                    "entity_type": "Work",
                                    "internal_id": str(identifier),
                                }
                            ],
                        },
                    },
                ]:
                    response = await client.post("/api/v1/catalog/works/validation", json=body)
                    assert response.status_code == 200 and response.json()["conforms"] is True, (
                        response.text
                    )
                assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
                assert "SET TRANSACTION READ ONLY" in statements
            finally:
                event.remove(engine, "before_cursor_execute", observe)
            assert await state(catalog.sessions, identifier) == before
            assert await catalog.read_public(identifier) is None
            valid = {"title": [{"main_title": [{"value": "Parity"}]}]}
            preview = await client.post(
                "/api/v1/catalog/works/validation",
                json={
                    "kind": "dto",
                    "profile_release": INSTITUTIONAL_PROFILE_ID,
                    "resource": valid,
                },
            )
            saved = await client.post("/api/v1/catalog/works", json=valid)
            assert saved.status_code == 201 and preview.json()["conforms"] is True
            # Same SHACL violation as a real revise, excluding request correlation.
            revision, graph = await catalog.read(identifier, actor=actor())
            graph.remove((URIRef(revision.identity.uri), BF.title, None))
            # Remove the orphaned Title to keep ownership valid.
            for node in list(graph.subjects(BF.mainTitle, None)):
                graph.remove((node, None, None))
            from libris.modules.bibliographic.application.diagnostics import (
                CatalogValidationRejected,
            )

            preview = await client.post(
                "/api/v1/catalog/works/validation",
                json={
                    "kind": "rdf",
                    "format": "turtle",
                    "root": revision.identity.uri,
                    "profile_release": INSTITUTIONAL_PROFILE_ID,
                    "document": graph.serialize(format="turtle"),
                },
            )
            assert preview.status_code == 200 and preview.json()["conforms"] is False
            with pytest.raises(CatalogValidationRejected) as error:
                await catalog.revise(
                    identifier,
                    revision.number,
                    graph,
                    Provenance("test", "parity"),
                    actor=actor(),
                )
            assert preview.json()["issues"] == error.value.result.public_dict()["issues"]
            assert await state(catalog.sessions, identifier) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "collection,target_collection,predicate,entity,target_entity",
    [
        ("works", "works", str(BF.relatedTo), "Work", "Work"),
        ("instances", "works", str(BF.instanceOf), "Instance", "Work"),
        ("items", "instances", str(BF.itemOf), "Item", "Instance"),
        ("agents", "agents", str(DCTERMS.relation), "Agent", "Agent"),
        (
            "subjects",
            "subjects",
            "http://www.w3.org/2004/02/skos/core#related",
            "Subject",
            "Subject",
        ),
    ],
)
def test_relationship_writes_all_five_entities(
    collection: str,
    target_collection: str,
    predicate: str,
    entity: str,
    target_entity: str,
) -> None:
    async def scenario() -> None:
        async with client_context() as (client, catalog):
            source, target = (
                await create(client, collection),
                await create(client, target_collection),
            )
            link = relationship(source.json(), target.json(), predicate, entity)
            link["target"]["entity_type"] = target_entity
            path = source.headers["location"] + "/relationships"
            operation = {"op": "replace", "predicate": predicate, "links": [link]}
            response = await patch_links(client, path, [operation])
            assert response.status_code == 200, response.text
            assert any(
                item["target"]["internal_id"] == target.json()["internal_id"]
                for item in response.json()["links"]
                if item["predicate"] == predicate
            )
            identifier = UUID(source.json()["internal_id"])
            after = await state(catalog.sessions, identifier)
            assert after.current.number == 2 and len(after.events) == 2
            assert after.current.actor_id == actor().actor_id

    asyncio.run(scenario())
