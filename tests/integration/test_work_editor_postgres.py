"""M07.01: partial editor writes and reconciliation against disposable PostgreSQL."""

import asyncio
from uuid import UUID

import pytest
from rdflib import Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, XSD
from test_audit_postgres import actor
from test_catalog_http_postgres import client_context, create

from libris.modules.bibliographic.domain.models import Provenance

pytestmark = pytest.mark.integration


def test_work_editor_partial_round_trip_and_explicit_reconciliation() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            created = await create(client)
            path = created.headers["location"]
            identifier = UUID(created.json()["internal_id"])
            revision, graph = await service.read(identifier, actor=actor())
            extension = (
                URIRef(revision.identity.uri),
                DCTERMS.abstract,
                Literal("Preserved extension", datatype=XSD.string),
            )
            graph.add(extension)
            await service.revise(
                identifier, revision.number, graph, Provenance("test", "m0701"), actor=actor()
            )
            loaded = await client.get(path)
            original = loaded.json()
            before, original_graph = await service.read(identifier, actor=actor())
            # Two clients loaded the same ETag. First changes notes only.
            first = await client.patch(
                path,
                headers={"If-Match": loaded.headers["etag"]},
                json={
                    "note": [
                        {
                            "label": [
                                {"value": "Primeira edição", "language": "pt"},
                                {"value": "First edit", "language": "en"},
                            ],
                            "note_type": ["summary", "bibliography"],
                        }
                    ]
                },
            )
            assert first.status_code == 200, first.text
            local_title = [
                {
                    "kind": "Title",
                    "main_title": [
                        {"value": "Segunda edição", "language": "pt"},
                        {"value": "Second edit", "language": "en"},
                    ],
                }
            ]
            second = await client.patch(
                path, headers={"If-Match": loaded.headers["etag"]}, json={"title": local_title}
            )
            assert second.status_code == 412
            current = await client.get(path)
            assert current.json() == first.json()
            assert current.headers["etag"] == first.headers["etag"]
            # Explicit reconciliation sends only the local title against current ETag.
            reconciled = await client.patch(
                path, headers={"If-Match": current.headers["etag"]}, json={"title": local_title}
            )
            assert reconciled.status_code == 200, reconciled.text
            reloaded = await client.get(path)
            assert reloaded.json() == reconciled.json()
            assert reloaded.json()["note"] == first.json()["note"]
            for name in (
                "internal_id",
                "uri",
                "language",
                "contribution",
                "subject",
                "classification",
                "content",
                "related_to",
                "translation_of",
            ):
                assert reloaded.json()[name] == original[name]
            after, complete = await service.read(identifier, actor=actor())
            assert after.number == before.number + 2
            assert after.profile == before.profile
            assert after.identity == before.identity
            assert extension in complete
            _, historical = await service.read(identifier, number=before.number, actor=actor())
            assert isomorphic(historical, original_graph)
            # Optional null and [] both explicitly clear; omitted properties survive.
            for value in (None, []):
                cleared = await client.patch(
                    path, headers={"If-Match": reloaded.headers["etag"]}, json={"note": value}
                )
                assert cleared.status_code == 200, cleared.text
                assert cleared.json()["note"] == []
                reloaded = await client.get(path)
            revision_before_error, graph_before_error = await service.read(
                identifier, actor=actor()
            )
            invalid = await client.patch(
                path, headers={"If-Match": reloaded.headers["etag"]}, json={"title": []}
            )
            assert invalid.status_code == 422
            revision_after_error, graph_after_error = await service.read(identifier, actor=actor())
            assert revision_after_error.number == revision_before_error.number
            assert isomorphic(graph_before_error, graph_after_error)

    asyncio.run(scenario())
