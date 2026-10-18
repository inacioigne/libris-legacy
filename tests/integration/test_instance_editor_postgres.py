"""M07.02: Instance round-trip, local Work references and CAS on isolated PostgreSQL."""

import asyncio
from uuid import UUID, uuid4

import pytest
from rdflib import Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, XSD
from test_audit_postgres import actor
from test_catalog_http_postgres import client_context, create, payload

from libris.modules.bibliographic.domain.models import Provenance
from libris.modules.bibliographic.infrastructure.rdf import BF

pytestmark = pytest.mark.integration


def test_instance_editor_round_trip_references_and_concurrent_revision() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            work = await create(client)
            work_data = work.json()
            reference = {
                "kind": "local",
                "entity_type": "Work",
                "internal_id": work_data["internal_id"],
                "uri": work_data["uri"],
            }
            proposal = payload("instances")
            proposal["instance_of"] = reference
            proposal["title"] = [{"main_title": [{"value": "Edição", "language": "pt"}]}]
            proposal["note"] = [
                {"label": [{"value": "Nota", "language": "pt"}], "note_type": ["summary"]}
            ]
            response = await client.post("/api/v1/catalog/instances", json=proposal)
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
            publication = graph.value(URIRef(revision.identity.uri), BF.provisionActivity)
            assert publication is not None
            nested_extension = (publication, DCTERMS.description, Literal("Auxiliar preservado"))
            graph.add(nested_extension)
            await service.revise(
                identifier, revision.number, graph, Provenance("test", "m0702"), actor=actor()
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
                "edition_statement": [
                    {"value": "Terceira edição", "language": "pt"},
                    {"value": "Third edition", "language": "en"},
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
                "instance_of",
                "title",
                "provision_activity",
                "identified_by",
                "extent",
                "carrier",
            ):
                assert reloaded.json()[field] == original[field]
            assert reloaded.json()["instance_of"]["uri"] == work_data["uri"]
            assert (await client.get(work.headers["location"])).headers["etag"] == work.headers[
                "etag"
            ]
            after, complete = await service.read(identifier, actor=actor())
            assert after.number == before.number + 2
            assert after.profile == before.profile and after.identity == before.identity
            assert extension in complete
            publication_after = complete.value(URIRef(revision.identity.uri), BF.provisionActivity)
            assert publication_after is not None
            assert (
                publication_after, DCTERMS.description, Literal("Auxiliar preservado")
            ) in complete
            _, historical = await service.read(identifier, number=before.number, actor=actor())
            assert isomorphic(historical, original_graph)
            assert (await client.get(path + "/public")).status_code == 404
            editorial = (await client.get(path + "/editorial")).json()
            assert editorial["editorial_state"] == "draft" and editorial["public_revision"] is None
            # Failed references never leave orphan records or change the linked Work.
            for invalid in (
                {**reference, "internal_id": str(uuid4())},
                {**reference, "uri": "urn:wrong:work"},
                {**reference, "entity_type": "Instance"},
            ):
                rejected = await client.patch(
                    path,
                    headers={"If-Match": reloaded.headers["etag"]},
                    json={"instance_of": invalid},
                )
                assert rejected.status_code == 422, rejected.text
                rejected_create = await client.post(
                    "/api/v1/catalog/instances", json={"instance_of": invalid}
                )
                assert rejected_create.status_code == 422, rejected_create.text
            unchanged, unchanged_graph = await service.read(identifier, actor=actor())
            assert unchanged == after and isomorphic(unchanged_graph, complete)
            destructive = await client.patch(
                path,
                headers={"If-Match": reloaded.headers["etag"]},
                json={"provision_activity": []},
            )
            # The existing HTTP mapper classifies RdfDocumentError as 400;
            # preserve that public contract and verify the rollback separately.
            assert destructive.status_code == 400, destructive.text
            assert destructive.json()["code"] == "request.malformed"
            protected, protected_graph = await service.read(identifier, actor=actor())
            assert protected == after and isomorphic(protected_graph, complete)
            # Explicit reassignment uses the existing local-reference DTO in one PATCH.
            other_work = await create(client)
            other = other_work.json()
            reassigned = await client.patch(
                path,
                headers={"If-Match": reloaded.headers["etag"]},
                json={
                    "instance_of": {
                        "kind": "local",
                        "entity_type": "Work",
                        "internal_id": other["internal_id"],
                        "uri": other["uri"],
                    }
                },
            )
            assert reassigned.status_code == 200, reassigned.text
            assert reassigned.json()["instance_of"]["uri"] == other["uri"]
            for value in (None, []):
                cleared = await client.patch(
                    path, headers={"If-Match": reassigned.headers["etag"]}, json={"title": value}
                )
                assert cleared.status_code == 200, cleared.text
                assert cleared.json()["title"] == []
                assert cleared.json()["instance_of"]["uri"] == other["uri"]
                reassigned = cleared

    asyncio.run(scenario())
