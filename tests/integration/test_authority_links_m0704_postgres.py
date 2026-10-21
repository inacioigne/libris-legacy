"""M07.04: autoridades compartilhadas conservam snapshot, revisão e auditoria."""

import asyncio
from uuid import UUID, uuid4

import pytest
from libris.modules.identity.api.dependencies import get_current_actor
from rdflib.compare import isomorphic
from test_audit_postgres import actor
from test_catalog_http_postgres import client_context, create
from test_editorial_postgres import state

pytestmark = pytest.mark.integration
BF = "http://id.loc.gov/ontologies/bibframe/"


@pytest.mark.parametrize("collection,entity", [("agents", "Agent"), ("subjects", "Subject")])
def test_authority_revision_is_independent_of_link_and_unlink(collection: str, entity: str) -> None:
    async def scenario() -> None:
        async with client_context() as (client, catalog):
            authority = await create(client, collection)
            work = await create(client)
            authority_id = UUID(authority.json()["internal_id"])
            work_id = UUID(work.json()["internal_id"])
            original = await state(catalog.sessions, authority_id)
            revision, graph = await catalog.read(authority_id, actor=actor())
            work_before = await state(catalog.sessions, work_id)
            path = work.headers["location"] + "/relationships"
            link = {
                "kind": "contribution" if entity == "Agent" else "direct",
                "source": {"kind": "local", "entity_type": "Work", "internal_id": str(work_id)},
                "target": {
                    "kind": "local",
                    "entity_type": entity,
                    "internal_id": str(authority_id),
                    "uri": authority.json()["uri"],
                },
                "predicate": BF + ("contribution" if entity == "Agent" else "subject"),
            }
            if entity == "Agent":
                link["role"] = [
                    "http://id.loc.gov/vocabulary/relators/aut",
                    "http://id.loc.gov/vocabulary/relators/edt",
                ]
            app = client._transport.app
            app.dependency_overrides[get_current_actor] = lambda: actor("libris-cataloger")
            assert (await client.get(authority.headers["location"])).status_code == 200
            assert (await client.get(f"/api/v1/catalog/{collection}")).status_code == 200
            assert (
                await client.patch(
                    authority.headers["location"],
                    json={},
                    headers={"If-Match": authority.headers["etag"]},
                )
            ).status_code == 403
            initial = await client.get(path)
            body = {"operations": [{"op": "add", "links": [link]}]}
            assert (await client.patch(path, json=body)).status_code == 428
            added = await client.patch(
                path, json=body, headers={"If-Match": initial.headers["etag"]}
            )
            assert added.status_code == 200, added.text
            match = next(
                item
                for item in added.json()["links"]
                if item["target"].get("internal_id") == str(authority_id)
            )
            if entity == "Agent":
                assert set(match["role"]) == set(link["role"])
            assert added.headers["etag"] != initial.headers["etag"]
            after_add = await state(catalog.sessions, work_id)
            assert (
                await client.patch(path, json=body, headers={"If-Match": initial.headers["etag"]})
            ).status_code == 412
            assert (
                await client.patch(path, json=body, headers={"If-Match": added.headers["etag"]})
            ).status_code in {409, 422}
            for target in [
                dict(link["target"], internal_id=str(uuid4())),
                dict(link["target"], entity_type="Instance"),
                {
                    "kind": "unreconciled",
                    "entity_type": entity,
                    "namespace": "test",
                    "key": "pending",
                    "label": "Pending",
                },
            ]:
                invalid = {"operations": [{"op": "add", "links": [dict(link, target=target)]}]}
                failed = await client.patch(
                    path, json=invalid, headers={"If-Match": added.headers["etag"]}
                )
                assert failed.status_code == 422, failed.text
                assert await state(catalog.sessions, work_id) == after_add
            assert await state(catalog.sessions, authority_id) == original
            read = await client.get(path)
            assert read.json() == added.json()
            assert read.headers["etag"] == added.headers["etag"]
            removed = await client.patch(
                path,
                json={"operations": [{"op": "remove", "links": [match]}]},
                headers={"If-Match": read.headers["etag"]},
            )
            assert removed.status_code == 200, removed.text
            assert (await client.get(path)).json() == initial.json()
            assert await state(catalog.sessions, authority_id) == original
            current_revision, current_graph = await catalog.read(authority_id, actor=actor())
            assert current_revision == revision
            assert isomorphic(current_graph, graph)
            after = await state(catalog.sessions, work_id)
            assert after.current.number == work_before.current.number + 2
            assert len(after.events) == len(work_before.events) + 2
            assert after.state.published_revision == work_before.state.published_revision

            # The configured role has manage but lacks metadata_revision:read and
            # bibliographic:update. Do not silently broaden these grants in the UI.
            app.dependency_overrides[get_current_actor] = lambda: actor("libris-authority-manager")
            assert (await client.get(authority.headers["location"])).status_code == 403
            assert (await client.get(f"/api/v1/catalog/{collection}")).status_code == 403
            assert (
                await client.patch(path, json=body, headers={"If-Match": removed.headers["etag"]})
            ).status_code == 403
            updated = await client.patch(
                authority.headers["location"],
                json={},
                headers={"If-Match": authority.headers["etag"]},
            )
            assert updated.status_code == 200, updated.text
            app.dependency_overrides[get_current_actor] = lambda: actor("unknown")
            assert (await client.get(path)).status_code == 403
            assert (
                await client.patch(path, json=body, headers={"If-Match": removed.headers["etag"]})
            ).status_code == 403
            del app.dependency_overrides[get_current_actor]
            assert (await client.get(path)).status_code == 401
            assert (
                await client.patch(path, json=body, headers={"If-Match": removed.headers["etag"]})
            ).status_code == 401

    asyncio.run(scenario())
