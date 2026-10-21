"""M07.03: Item round-trip, local Instance references and CAS on isolated PostgreSQL."""

import asyncio
from uuid import UUID, uuid4

import pytest
from libris.modules.bibliographic.domain.models import Provenance
from libris.modules.bibliographic.infrastructure.rdf import BF
from rdflib import Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, XSD
from test_audit_postgres import actor
from test_catalog_http_postgres import client_context, create, payload

pytestmark = pytest.mark.integration


def test_item_editor_round_trip_references_and_concurrent_revision() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            work_parent = await create(client)
            work = await client.post("/api/v1/catalog/instances", json={
                "instance_of": {"kind": "local", "entity_type": "Work",
                                "internal_id": work_parent.json()["internal_id"]}
            })
            assert work.status_code == 201
            work_data = work.json()
            reference = {
                "kind": "local",
                "entity_type": "Instance",
                "internal_id": work_data["internal_id"],
                "uri": work_data["uri"],
            }
            proposal = payload("items")
            proposal["item_of"] = reference
            proposal["note"] = [
                {"label": [{"value": "Nota", "language": "pt"}], "note_type": ["summary"]}
            ]
            response = await client.post("/api/v1/catalog/items", json=proposal)
            assert response.status_code == 201, response.text
            path = response.headers["location"]
            identifier = UUID(response.json()["internal_id"])
            revision, graph = await service.read(identifier, actor=actor())
            extension = (
                URIRef(revision.identity.uri),
                DCTERMS.abstract,
                Literal("Preservada", datatype=XSD.string),
            )
            graph.add(extension)
            mark = graph.value(URIRef(revision.identity.uri), BF.shelfMark)
            assert mark is not None
            nested_extension = (mark, DCTERMS.description, Literal("Auxiliar preservado"))
            graph.add(nested_extension)
            await service.revise(
                identifier, revision.number, graph, Provenance("test", "m0703"), actor=actor()
            )
            loaded = await client.get(path)
            original = loaded.json()
            before, original_graph = await service.read(identifier, actor=actor())
            first = await client.patch(
                path,
                headers={"If-Match": loaded.headers["etag"]},
                json={"note": [{"label": [{"value": "Concurrent", "language": "en"}]}]},
            )
            assert first.status_code == 200, first.text
            local = {
                "custodial_history": [
                    {"value": "Custódia revista", "language": "pt"},
                    {"value": "Revised custody", "language": "en"},
                ]
            }
            stale = await client.patch(
                path, headers={"If-Match": loaded.headers["etag"]}, json=local
            )
            assert stale.status_code == 412
            current = await client.get(path)
            saved = await client.patch(
                path, headers={"If-Match": current.headers["etag"]}, json=local
            )
            assert saved.status_code == 200, saved.text
            reloaded = await client.get(path)
            assert reloaded.json() == saved.json()
            assert reloaded.json()["note"] == first.json()["note"]
            for field in (
                "internal_id",
                "uri",
                "item_of",
                "identified_by",
                "held_by",
                "shelf_mark",
                "provenance",
            ):
                assert reloaded.json()[field] == original[field]
            assert reloaded.json()["item_of"]["uri"] == work_data["uri"]
            assert (await client.get(work.headers["location"])).headers["etag"] == work.headers[
                "etag"
            ]
            after, complete = await service.read(identifier, actor=actor())
            assert after.number == before.number + 2
            assert after.profile == before.profile and after.identity == before.identity
            assert extension in complete
            mark_after = complete.value(URIRef(revision.identity.uri), BF.shelfMark)
            assert mark_after is not None
            assert (
                mark_after, DCTERMS.description, Literal("Auxiliar preservado")
            ) in complete
            _, historical = await service.read(identifier, number=before.number, actor=actor())
            assert isomorphic(historical, original_graph)
            assert (await client.get(path + "/public")).status_code == 404
            editorial = (await client.get(path + "/editorial")).json()
            assert editorial["editorial_state"] == "draft" and editorial["public_revision"] is None
            # Failed references never leave orphan records or change the linked Instance.
            for invalid in (
                {**reference, "internal_id": str(uuid4())},
                {**reference, "uri": "urn:wrong:instance"},
                {**reference, "entity_type": "Work"},
            ):
                rejected = await client.patch(
                    path,
                    headers={"If-Match": reloaded.headers["etag"]},
                    json={"item_of": invalid},
                )
                assert rejected.status_code == 422, rejected.text
                rejected_create = await client.post(
                    "/api/v1/catalog/items", json={**proposal, "item_of": invalid}
                )
                assert rejected_create.status_code == 422, rejected_create.text
            unchanged, unchanged_graph = await service.read(identifier, actor=actor())
            assert unchanged == after and isomorphic(unchanged_graph, complete)
            destructive = await client.patch(
                path,
                headers={"If-Match": reloaded.headers["etag"]},
                json={"shelf_mark": []},
            )
            # The existing HTTP mapper classifies RdfDocumentError as 400;
            # preserve that public contract and verify the rollback separately.
            assert destructive.status_code == 400, destructive.text
            assert destructive.json()["code"] == "request.malformed"
            protected, protected_graph = await service.read(identifier, actor=actor())
            assert protected == after and isomorphic(protected_graph, complete)
            # Inverses come from actual stored child relationships, not title matching.
            instance_links = await client.get(
                work.headers["location"] + "/relationships?include_inverses=true"
            )
            assert instance_links.status_code == 200, instance_links.text
            assert any(
                link["predicate"] == str(BF.hasItem)
                and link["target"]["internal_id"] == str(identifier)
                for link in instance_links.json()["links"]
            )
            work_links = await client.get(
                work_parent.headers["location"] + "/relationships?include_inverses=true"
            )
            assert any(
                link["predicate"] == str(BF.hasInstance)
                and link["target"]["internal_id"] == work_data["internal_id"]
                for link in work_links.json()["links"]
            )
            for value in (None, []):
                cleared = await client.patch(
                    path, headers={"If-Match": reloaded.headers["etag"]},
                    json={"custodial_history": value},
                )
                assert cleared.status_code == 200, cleared.text
                assert cleared.json()["custodial_history"] == []
                assert cleared.json()["item_of"]["uri"] == work_data["uri"]
                reloaded = cleared
            # A datatype missing from the DTO cannot be silently erased even
            # when the user edits another value in that same group.
            latest, typed_graph = await service.read(identifier, actor=actor())
            typed = (
                URIRef(latest.identity.uri), BF.custodialHistory,
                Literal("Typed custody", datatype=XSD.string),
            )
            typed_graph.add(typed)
            await service.revise(
                identifier, latest.number, typed_graph, Provenance("test", "typed"),
                actor=actor(),
            )
            typed_loaded = await client.get(path)
            rejected = await client.patch(
                path, headers={"If-Match": typed_loaded.headers["etag"]},
                json={"custodial_history": [{"value": "Replacement"}]},
            )
            assert rejected.status_code == 400, rejected.text
            assert (await client.get(path)).headers["etag"] == typed_loaded.headers["etag"]
            allowed = await client.patch(
                path, headers={"If-Match": typed_loaded.headers["etag"]}, json={"note": []},
            )
            assert allowed.status_code == 200, allowed.text
            _, preserved_typed = await service.read(identifier, actor=actor())
            assert typed in preserved_typed

    asyncio.run(scenario())
