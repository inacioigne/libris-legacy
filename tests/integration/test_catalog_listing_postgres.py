"""M05.06: actual PostgreSQL queries, publication selection and semantic HTTP export."""

import asyncio
from uuid import UUID, uuid4

import pytest
from libris.modules.bibliographic.domain.models import Provenance
from libris.modules.bibliographic.infrastructure.serialization import (
    parse_representation,
)
from rdflib import Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, XSD
from sqlalchemy import event
from test_audit_postgres import actor
from test_catalog_http_postgres import client_context
from test_editorial_http_postgres import command, seed

pytestmark = pytest.mark.integration
COLLECTIONS = ["works", "instances", "items", "agents", "subjects"]


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_list_and_export_exact_published_revision(collection: str) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            client.headers.pop("accept", None)
            first = await seed(client, collection)
            path, identifier = (
                first.headers["location"],
                UUID(first.json()["internal_id"]),
            )
            listing = f"/api/v1/catalog/{collection}"
            public_listing = f"/api/v1/catalog/public/{collection}"
            query = {"internal_id": str(identifier)}
            assert (await client.get(public_listing, params=query)).json()["items"] == []
            assert (await client.get(listing, params=query)).json()["items"] == [first.json()]
            # Add integral snapshot extensions outside Read, with language, datatype,
            # multiple values and an IRI object. They must survive both RDF formats.
            revision, graph = await service.read(identifier, actor=actor())
            root = URIRef(revision.identity.uri)
            graph.add(
                (
                    root,
                    DCTERMS.abstract,
                    Literal("Descrição pública – ação", lang="pt-BR"),
                )
            )
            graph.add((root, DCTERMS.abstract, Literal("Published description", lang="en")))
            graph.add((root, DCTERMS.created, Literal("2026-10-16", datatype=XSD.date)))
            graph.add((root, DCTERMS.source, URIRef("https://example.org/external/ação")))
            revision = await service.revise(
                identifier,
                revision.number,
                graph,
                Provenance("test", "issue50"),
                actor=actor(),
            )
            for operation in ["submit", "approve"]:
                response = await command(client, path, operation, selected=revision.number)
                assert response.status_code == 200, response.text
                assert (await client.get(public_listing, params=query)).json()["items"] == []
                assert (await client.get(path + "/public")).status_code == 404
            response = await command(client, path, "publish", selected=revision.number)
            assert response.status_code == 200, response.text
            published = await client.get(path + "/public")
            assert published.status_code == 200
            # The public projection is exactly the selected Read, with no private metadata.
            assert published.json() == first.json()
            published_etag = published.headers["etag"]
            current = await client.get(path)
            field = (
                "variant_name"
                if collection == "agents"
                else ("alternative_label" if collection == "subjects" else "note")
            )
            value = [{"value": "PRIVATE CANDIDATE", "language": "en"}]
            patch_value = value if collection in {"agents", "subjects"} else [{"label": value}]
            changed = await client.patch(
                path,
                json={field: patch_value},
                headers={
                    "If-Match": current.headers["etag"],
                },
            )
            assert changed.status_code == 200, changed.text
            assert "PRIVATE CANDIDATE" in changed.text
            assert (await client.get(listing, params=query)).json()["items"] == [changed.json()]
            assert (await client.get(public_listing, params=query)).json()["items"] == [
                first.json()
            ]
            assert (await client.get(path + "/public")).headers["etag"] == published_etag
            assert (
                await client.get(public_listing, params=query | {"profile": revision.profile})
            ).json()["items"]
            assert not (
                await client.get(public_listing, params=query | {"profile": "absent"})
            ).json()["items"]
            assert (await client.get(public_listing, params={"uri": first.json()["uri"]})).json()[
                "items"
            ] == [first.json()]
            assert not (
                await client.get(
                    public_listing, params=query | {"uri": "https://example.org/absent"}
                )
            ).json()["items"]
            for operation in ["submit", "approve"]:
                response = await command(
                    client, path, operation, selected=revision.number + 1, scope="candidate"
                )
                assert response.status_code == 200, response.text
                assert (await client.get(public_listing, params=query)).json()["items"] == [
                    first.json()
                ]
            for suffix in ["", "/public"]:
                exports = []
                for media, syntax in [
                    ("application/ld+json", "json-ld"),
                    ("text/turtle", "turtle"),
                ]:
                    response = await client.get(path + suffix, headers={"Accept": media})
                    assert response.status_code == 200, response.text
                    assert response.headers["content-type"].split(";")[0] == media
                    assert "Accept" in response.headers["vary"].split(", ")
                    recovered = parse_representation(response.text, syntax)
                    exports.append(recovered)
                    if suffix:
                        assert isomorphic(recovered, graph)
                        assert "PRIVATE CANDIDATE" not in response.text
                        assert response.headers["etag"] != published_etag
                    else:
                        assert "PRIVATE CANDIDATE" in response.text
                    # An RDF ETag never passes the JSON editing precondition.
                    rejected = await client.patch(
                        path,
                        json={},
                        headers={
                            "If-Match": response.headers["etag"],
                        },
                    )
                    assert rejected.status_code == 412
                assert isomorphic(*exports)
                for accept, expected in [
                    ("text/turtle;q=0.9,application/json;q=0.2", "text/turtle"),
                    ("*/*", "application/json"),
                    ("application/*", "application/json"),
                    ("application/json;q=0,*/*;q=0.5", "application/ld+json"),
                ]:
                    response = await client.get(path + suffix, headers={"Accept": accept})
                    assert response.headers["content-type"].split(";")[0] == expected
                for accept in ["text/html", "*/*;q=0", "text/turtle;q=9", ""]:
                    response = await client.get(path + suffix, headers={"Accept": accept})
                    assert response.status_code == 406
                    assert response.headers["content-type"] == "application/problem+json"
                    assert "Accept" in response.headers["vary"].split(", ")
            # Withdrawn/archived records and historical publication never leak.
            for operation in ["withdraw", "archive"]:
                response = await command(client, path, operation, selected=revision.number)
                assert response.status_code == 200, response.text
                assert (await client.get(public_listing, params=query)).json()["items"] == []
                for media in ["application/json", "application/ld+json", "text/turtle"]:
                    response = await client.get(path + "/public", headers={"Accept": media})
                    assert response.status_code == 404
            assert (
                await client.get(listing, params=query | {"editorial_state": "archived"})
            ).json()["items"]
            assert not (
                await client.get(listing, params=query | {"editorial_state": "draft"})
            ).json()["items"]
            # Unknown / wrong collection are uniformly hidden, even for RDF.
            other = "agents" if collection != "agents" else "works"
            assert (
                await client.get(path.replace(collection, other) + "/public")
            ).status_code == 404
            assert (await client.get(f"{listing}/{uuid4()}/public")).status_code == 404

    asyncio.run(scenario())


def test_keyset_pages_static_and_concurrent_changes_with_constant_sql_cost() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            listing = "/api/v1/catalog/works"

            # Exact expected set includes any rows created by other integration tests.
            async def traverse(limit: int) -> list[str]:
                found = []
                query: dict[str, str | int] = {"limit": limit}
                while True:
                    response = await client.get(listing, params=query)
                    assert response.status_code == 200, response.text
                    page = response.json()
                    found.extend(row["internal_id"] for row in page["items"])
                    if page["next_cursor"] is None:
                        return found
                    query["cursor"] = page["next_cursor"]

            baseline = await traverse(100)
            created = [await seed(client) for _ in range(7)]  # identical titles
            expected = sorted(baseline + [r.json()["internal_id"] for r in created])
            statements: list[str] = []
            async with service.sessions() as session:
                engine = session.get_bind()

            def record(
                conn: object,
                cursor: object,
                statement: str,
                parameters: object,
                context: object,
                executemany: bool,
            ) -> None:
                statements.append(statement)

            event.listen(engine, "before_cursor_execute", record)
            try:
                actual = await traverse(2)
            finally:
                event.remove(engine, "before_cursor_execute", record)
            assert actual == expected
            assert len(actual) == len(set(actual))
            assert len(statements) == (len(expected) + 1) // 2
            assert all("LIMIT" in sql and "ORDER BY" in sql for sql in statements)
            assert await traverse(1) == expected
            assert await traverse(100) == expected
            empty = (await client.get(listing, params={"internal_id": str(uuid4())})).json()
            assert empty["items"] == [] and empty["next_cursor"] is None
            first = (await client.get(listing, params={"limit": 2})).json()
            cursor = first["next_cursor"]
            boundary = first["items"][-1]["internal_id"]
            # Updates do not move an immutable UUID through the ordering.
            target = first["items"][0]["internal_id"]
            current = await client.get(f"{listing}/{target}")
            revised = await client.patch(
                f"{listing}/{target}",
                json={},
                headers={
                    "If-Match": current.headers["etag"],
                },
            )
            assert revised.status_code == 200
            from libris.modules.bibliographic.api.schemas import WorkCreate
            from libris.modules.bibliographic.domain.models import ResourceIdentity
            from libris.modules.bibliographic.infrastructure.validation import (
                INSTITUTIONAL_PROFILE_ID,
            )
            from libris.modules.identity.application.authorization import HumanContext

            inserted = []
            for delta in [-1, 1]:
                candidate = UUID(boundary).int + delta
                while str(UUID(int=candidate)) in expected:
                    candidate += delta
                new_id = UUID(int=candidate)
                identity = ResourceIdentity(new_id, f"https://seek50.example.invalid/{new_id}")
                await service.create_resource(
                    WorkCreate.model_validate({"title": [{"main_title": [{"value": "Repeated"}]}]}),
                    identity, INSTITUTIONAL_PROFILE_ID, HumanContext(actor()),
                )
                inserted.append(str(new_id))
            remaining: list[str] = []
            while cursor:
                page = (await client.get(listing, params={"limit": 2, "cursor": cursor})).json()
                remaining.extend(row["internal_id"] for row in page["items"])
                cursor = page["next_cursor"]
            assert remaining == [id for id in sorted(expected + inserted) if id > boundary]
            # Scope changes are rejected; a cursor is never permission to read drafts.
            for path, params in [
                (listing, {"limit": 3}),
                (listing, {"limit": 2, "profile": "other"}),
                ("/api/v1/catalog/public/works", {"limit": 2}),
                ("/api/v1/catalog/agents", {"limit": 2}),
            ]:
                response = await client.get(path, params=params | {"cursor": first["next_cursor"]})
                assert response.status_code == 400

    asyncio.run(scenario())


def test_listing_does_not_infer_state_for_legacy() -> None:
    from datetime import UTC, datetime

    from libris.modules.bibliographic.api.schemas import WorkCreate
    from libris.modules.bibliographic.application.service import prepare_document
    from libris.modules.bibliographic.domain.models import ResourceIdentity, Revision
    from libris.modules.bibliographic.infrastructure.serialization import (
        create_to_graph,
    )
    from libris.modules.bibliographic.infrastructure.validation import (
        INSTITUTIONAL_PROFILE_ID,
    )

    async def scenario() -> None:
        async with client_context() as (client, service):
            identity = ResourceIdentity.create("https://legacy50.example.invalid/resources")
            graph = create_to_graph(
                WorkCreate.model_validate({"title": [{"main_title": [{"value": "Legado"}]}]}),
                identity,
                profile=INSTITUTIONAL_PROFILE_ID,
            )
            revision = Revision(
                identity,
                1,
                datetime.now(UTC),
                Provenance("legacy", "test"),
                INSTITUTIONAL_PROFILE_ID,
            )
            async with service.sessions() as session, session.begin():
                await service.store.create(session, identity)
                await service.store.save(
                    session, revision, prepare_document(graph, identity.uri, revision.profile)
                )
            query = {"internal_id": str(identity.internal_id)}
            path = "/api/v1/catalog/works"
            assert (await client.get(path, params=query)).json()["items"]
            assert not (await client.get(path, params=query | {"editorial_state": "draft"})).json()[
                "items"
            ]
            assert not (await client.get("/api/v1/catalog/public/works", params=query)).json()[
                "items"
            ]
            assert (await client.get(f"{path}/{identity.internal_id}/public")).status_code == 404
            async with service.sessions() as session:
                assert await service.editorial.get(session, identity.internal_id) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["Agent", "Person", "Organization"])
def test_agent_subtypes_and_parameterized_filters(kind: str) -> None:
    async def scenario() -> None:
        async with client_context() as (client, _service):
            response = await client.post("/api/v1/catalog/agents", json={
                "kind": kind, "authorized_name": [{"value": "Nome repetido", "language": "pt"}],
            })
            assert response.status_code == 201, response.text
            query = {"internal_id": response.json()["internal_id"]}
            page = await client.get("/api/v1/catalog/agents", params=query)
            assert page.status_code == 200
            assert page.json()["items"] == [response.json()]
            page = await client.get("/api/v1/catalog/agents", params=query | {
                "profile": "' OR 1=1 --",
            })
            assert page.status_code == 200 and page.json()["items"] == []

    asyncio.run(scenario())
