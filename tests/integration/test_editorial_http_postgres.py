"""M05.05 HTTP behavior on disposable PostgreSQL; trusted actor override isolates OIDC."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from libris.modules.bibliographic.api.editorial import get_editorial_service
from libris.modules.bibliographic.application.catalog_editorial import (
    CatalogEditorialService,
)
from libris.modules.bibliographic.infrastructure.persistence import SemanticRevision
from libris.modules.bibliographic.infrastructure.rdf import parse_document
from libris.modules.identity.api.dependencies import get_current_actor
from rdflib.compare import isomorphic
from sqlalchemy import select
from test_audit_postgres import actor
from test_catalog_http_postgres import client_context, payload
from test_editorial_postgres import state

pytestmark = pytest.mark.integration


async def seed(client: httpx.AsyncClient, collection: str = "works") -> httpx.Response:
    if collection == "works":
        data = {"title": [{"main_title": [{"value": "Published R1"}]}]}
    elif collection == "agents":
        data = payload(collection)
    elif collection == "subjects":
        data = payload(collection)
        data["broader"] = []
    else:
        parent = await seed(client, "works" if collection == "instances" else "instances")
        data = (
            {"instance_of": {"uri": parent.json()["uri"]}}
            if collection == "instances"
            else {
                "item_of": {"uri": parent.json()["uri"]},
                "identified_by": [{"kind": "Local", "value": "ISSUE49"}],
            }
        )
    response = await client.post(f"/api/v1/catalog/{collection}", json=data)
    assert response.status_code == 201, response.text
    return response


async def command(
    client: httpx.AsyncClient,
    path: str,
    operation: str,
    selected: int = 1,
    scope: str = "record",
    *,
    etag: str | None = None,
) -> httpx.Response:
    etag = etag or (await client.get(path + "/editorial")).headers["etag"]
    return await client.post(
        path + "/editorial/" + operation,
        json={
            "selected_revision": selected,
            "scope": scope,
            "justification": "Synthetic test",
        },
        headers={"If-Match": etag, "actor_id": "forged", "roles": "admin"},
    )


async def publish(
    client: httpx.AsyncClient, path: str, selected: int = 1, scope: str = "record"
) -> None:
    for op in ("submit", "approve", "publish"):
        response = await command(client, path, op, selected, scope)
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("collection", ["works", "instances", "items", "agents", "subjects"])
def test_http_publication_candidate_history_restore_and_archive(
    collection: str,
) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await seed(client, collection)
            path, identifier = (
                first.headers["location"],
                UUID(first.json()["internal_id"]),
            )
            public = path + "/public"
            initial = await client.get(path + "/editorial")
            assert initial.headers["etag"] == first.headers["etag"]
            assert initial.json()["editorial_state"] == "draft"
            assert initial.json()["published_revision"] is None
            absent = await client.get(public)
            assert absent.status_code == 404
            assert absent.json()["code"] == "resource.unknown"
            assert (await command(client, path, "approve")).status_code == 409
            await publish(client, path)
            assert (await client.get(public)).json() == first.json()
            published_etag = (await client.get(path)).headers["etag"]
            patched = await client.patch(path, json={}, headers={"If-Match": published_etag})
            assert patched.status_code == 200
            candidate = (await client.get(path + "/editorial")).json()
            assert candidate["current_revision"] == candidate["candidate_revision"] == 2
            assert candidate["published_revision"] == candidate["public_revision"] == 1
            assert candidate["approved_revision"] is None
            assert (await client.get(public)).json() == first.json()
            # Metadata queries, full snapshot, isomorphic comparison and immutable restoration.
            snapshot = await client.get(path + "/revisions/1/snapshot")
            assert snapshot.status_code == 200 and snapshot.headers["content-type"].startswith(
                "text/turtle"
            )
            original_graph = parse_document(snapshot.text, "turtle")
            diff = await client.get(path + "/revisions/compare?previous=1&current=2")
            assert diff.status_code == 200 and diff.json() == {
                "added": [],
                "removed": [],
            }
            restored = await client.post(
                path + "/revisions/restore",
                json={"source_revision": 1},
                headers={"If-Match": patched.headers["etag"]},
            )
            assert restored.status_code == 201, restored.text
            assert restored.json()["number"] == 3
            assert restored.json()["actor_id"] == actor().actor_id
            assert (await client.get(path + "/editorial")).json()["public_revision"] == 1
            assert isomorphic(
                original_graph,
                parse_document((await client.get(path + "/revisions/1/snapshot")).text, "turtle"),
            )
            # Return applies to the candidate; edits while approved fail.
            for op in ("submit", "approve"):
                assert (await command(client, path, op, 3, "candidate")).status_code == 200
            current = await client.get(path)
            assert (
                await client.patch(path, json={}, headers={"If-Match": current.headers["etag"]})
            ).status_code == 409
            assert (await command(client, path, "return", 3, "candidate")).status_code == 200
            await publish(client, path, 3, "candidate")
            assert (await client.get(path + "/editorial")).json()["public_revision"] == 3
            assert (await command(client, path, "withdraw", 3)).status_code == 200
            assert (await client.get(public)).status_code == 404
            # Named publish must never turn into republish, even for an administrator.
            assert (await command(client, path, "publish", 3)).status_code == 409
            assert (await command(client, path, "republish", 3)).status_code == 200
            assert (await client.get(public)).status_code == 200
            assert (await command(client, path, "withdraw", 3)).status_code == 200
            assert (await command(client, path, "archive", 3)).status_code == 200
            assert (await client.get(public)).status_code == 404
            assert (await command(client, path, "republish", 3)).status_code == 409
            assert (await client.get(path + "/editorial")).json()["editorial_state"] == "archived"
            numbers = []
            after = 0
            while True:
                page = (await client.get(path + f"/revisions?limit=1&after_number={after}")).json()
                if not page["items"]:
                    break
                numbers += [entry["revision"]["number"] for entry in page["items"]]
                after = page["next_after_number"]
            assert numbers == [1, 2, 3]
            assert (await client.get(path + "/revisions/1")).json()["number"] == 1
            assert (await client.get(path + "/revisions/999")).status_code == 404
            assert (
                await client.get(path + "/revisions/compare?previous=1&current=999")
            ).status_code == 404
            events, cursor = [], None
            while True:
                params = {"limit": 2}
                if cursor:
                    params |= {
                        "after_time": cursor["occurred_at"],
                        "after_event_id": cursor["event_id"],
                    }
                response = await client.get(path + "/audit", params=params)
                assert response.status_code == 200, response.text
                page = response.json()
                if not page["items"]:
                    break
                events += page["items"]
                cursor = page["next_cursor"]
            assert len({event["event_id"] for event in events}) == len(events)
            assert all(event["actor_id"] == actor().actor_id for event in events)
            restoration = next(e for e in events if e["restoration_kind"] == "content")
            assert restoration["source_revision"] == 1 and restoration["revision_number"] == 3
            for event in events:
                assert (await client.get(path + "/audit/" + event["event_id"])).json() == event
            history = await client.get(path + "/history")
            assert history.json()["events"] == events
            before = await state(service.sessions, identifier)
            # Public never needs OIDC and reveals no internal metadata.
            app = client._transport.app
            app.dependency_overrides.pop(get_current_actor)
            assert (await client.get(public)).status_code == 404
            for endpoint in (
                "editorial",
                "history",
                "audit",
                "revisions",
                "revisions/1/snapshot",
            ):
                assert (await client.get(path + "/" + endpoint)).status_code == 401
            assert await state(service.sessions, identifier) == before

    asyncio.run(scenario())


def test_http_public_r1_stays_until_r2_published_and_failed_integrity_rolls_back() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await seed(client)
            path, identifier = (
                first.headers["location"],
                UUID(first.json()["internal_id"]),
            )
            await publish(client, path)
            current = await client.get(path)
            patched = await client.patch(
                path,
                json={
                    "title": [{"main_title": [{"value": "Candidate secret R2"}]}],
                    "subject": [{"uri": "https://unclassified.invalid/subject"}],
                },
                headers={"If-Match": current.headers["etag"]},
            )
            assert patched.status_code == 200
            for op in ("submit", "approve"):
                assert (await command(client, path, op, 2, "candidate")).status_code == 200
            before = await state(service.sessions, identifier)
            response = await command(client, path, "publish", 2, "candidate")
            assert response.status_code == 422 and response.json()["code"] == "reference.unverified"
            assert await state(service.sessions, identifier) == before
            assert "Candidate secret" not in (await client.get(path + "/public")).text
            assert (await command(client, path, "return", 2, "candidate")).status_code == 200
            current = await client.get(path)
            assert (
                await client.patch(
                    path,
                    json={"subject": []},
                    headers={"If-Match": current.headers["etag"]},
                )
            ).status_code == 200
            await publish(client, path, 3, "candidate")
            app = client._transport.app
            app.dependency_overrides.pop(get_current_actor)
            public = await client.get(path + "/public")
            assert public.status_code == 200 and "Candidate secret R2" in public.text
            assert "actor_id" not in public.json() and "editorial_version" not in public.json()
            missing = await client.get(f"/api/v1/catalog/works/{uuid4()}/public")
            assert missing.status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["submit", "restore"])
def test_http_late_audit_failure_rolls_back_state_snapshot_and_event(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await seed(client)
            path, identifier = (
                first.headers["location"],
                UUID(first.json()["internal_id"]),
            )
            before = await state(service.sessions, identifier)
            adapter = CatalogEditorialService(service.sessions)
            client._transport.app.dependency_overrides[get_editorial_service] = lambda: adapter
            if operation == "restore":
                store = adapter.audit
            else:
                from libris.modules.bibliographic.infrastructure.audit import AuditStore

                store = AuditStore
            original = store.append

            async def fail(*args: object, **kwargs: object) -> None:
                await original(*args, **kwargs)
                raise RuntimeError("Synthetic failure after real INSERT")

            with monkeypatch.context() as scoped:
                scoped.setattr(store, "append", fail)
                response = (
                    await command(client, path, "submit")
                    if operation == "submit"
                    else await client.post(
                        path + "/revisions/restore",
                        json={"source_revision": 1},
                        headers={"If-Match": first.headers["etag"]},
                    )
                )
                assert response.status_code == 500, response.text
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

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["submit", "restore"])
def test_http_two_requests_with_same_etag_have_one_winner(operation: str) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await seed(client)
            path, identifier = (
                first.headers["location"],
                UUID(first.json()["internal_id"]),
            )
            adapter = CatalogEditorialService(service.sessions)
            client._transport.app.dependency_overrides[get_editorial_service] = lambda: adapter
            observed, count, original = asyncio.Event(), 0, adapter.observe

            async def barrier(*args: object, **kwargs: object) -> object:
                nonlocal count
                view = await original(*args, **kwargs)
                count += 1
                if count == 2:
                    observed.set()
                await asyncio.wait_for(observed.wait(), 10)
                return view

            adapter.observe = barrier
            if operation == "submit":
                responses = await asyncio.gather(
                    *[command(client, path, "submit", etag=first.headers["etag"]) for _ in range(2)]
                )
                assert sorted(r.status_code for r in responses) == [200, 412]
            else:
                responses = await asyncio.gather(
                    *[
                        client.post(
                            path + "/revisions/restore",
                            json={"source_revision": 1},
                            headers={"If-Match": first.headers["etag"]},
                        )
                        for _ in range(2)
                    ]
                )
                assert sorted(r.status_code for r in responses) == [201, 412]
            after = await state(service.sessions, identifier)
            assert after.state.version == 2 and len(after.events) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "role,read_status,audit_status,submit_status",
    [
        ("libris-reader", 403, 403, 403),
        ("libris-cataloger", 200, 403, 200),
        ("libris-reviewer", 200, 200, 403),
        ("libris-publisher", 200, 200, 403),
        ("libris-authority-manager", 403, 403, 403),
    ],
)
def test_http_effective_role_matrix(
    role: str, read_status: int, audit_status: int, submit_status: int
) -> None:
    async def scenario() -> None:
        async with client_context() as (client, _service):
            first = await seed(client)
            path = first.headers["location"]
            client._transport.app.dependency_overrides[get_current_actor] = lambda: actor(role)
            assert (await client.get(path + "/editorial")).status_code == read_status
            assert (await client.get(path + "/revisions")).status_code == read_status
            assert (await client.get(path + "/audit")).status_code == audit_status
            assert (
                await command(client, path, "submit", etag=first.headers["etag"])
            ).status_code == submit_status
            assert (
                await command(client, path, "publish", etag=first.headers["etag"])
            ).status_code == (409 if role == "libris-publisher" else 403)
            assert (
                await command(client, path, "approve", etag=first.headers["etag"])
            ).status_code == (409 if role == "libris-reviewer" else 403)

    asyncio.run(scenario())


def test_http_shacl_failure_preserves_publication_and_safe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from libris.modules.bibliographic.application.editorial_postgres import (
        PostgresEditorialTransaction,
    )
    from libris.modules.bibliographic.infrastructure.rdf import BF

    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await seed(client)
            path, identifier = (
                first.headers["location"],
                UUID(first.json()["internal_id"]),
            )
            await publish(client, path)
            current = await client.get(path)
            assert (
                await client.patch(path, json={}, headers={"If-Match": current.headers["etag"]})
            ).status_code == 200
            for op in ("submit", "approve"):
                assert (await command(client, path, op, 2, "candidate")).status_code == 200
            before = await state(service.sessions, identifier)
            original = PostgresEditorialTransaction.publication_context

            async def invalid(*args: object, **kwargs: object) -> object:
                context = await original(*args, **kwargs)
                from rdflib import Literal

                for title in list(context.graph.subjects(BF.mainTitle, None)):
                    context.graph.set((title, BF.mainTitle, Literal(3)))
                return replace(context, graph=context.graph)

            with monkeypatch.context() as scoped:
                scoped.setattr(PostgresEditorialTransaction, "publication_context", invalid)
                response = await command(client, path, "publish", 2, "candidate")
            assert response.status_code == 422, response.text
            assert "validation" in response.json(), response.text
            assert response.json()["validation"]["conforms"] is False
            assert response.json()["issues"]
            assert response.json()["request_id"] == response.json()["validation"]["request_id"]
            assert await state(service.sessions, identifier) == before
            assert (await client.get(path + "/public")).json() == first.json()

    asyncio.run(scenario())


def test_http_publish_races_with_edit_and_keeps_exact_approval() -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await seed(client)
            path, identifier = (
                first.headers["location"],
                UUID(first.json()["internal_id"]),
            )
            await publish(client, path)
            current = await client.get(path)
            assert (
                await client.patch(path, json={}, headers={"If-Match": current.headers["etag"]})
            ).status_code == 200
            for op in ("submit", "approve"):
                assert (await command(client, path, op, 2, "candidate")).status_code == 200
            before = await state(service.sessions, identifier)
            tag = (await client.get(path)).headers["etag"]
            adapter = CatalogEditorialService(service.sessions)
            client._transport.app.dependency_overrides[get_editorial_service] = lambda: adapter
            ready, count = asyncio.Event(), 0
            observe, current_method = adapter.observe, service.current

            async def wait() -> None:
                nonlocal count
                count += 1
                if count == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 10)

            async def observed(*args: object, **kwargs: object) -> object:
                view = await observe(*args, **kwargs)
                await wait()
                return view

            async def read(*args: object, **kwargs: object) -> object:
                view = await current_method(*args, **kwargs)
                await wait()
                return view

            adapter.observe, service.current = observed, read
            publication, edit = await asyncio.gather(
                command(client, path, "publish", 2, "candidate", etag=tag),
                client.patch(
                    path,
                    json={"title": [{"main_title": [{"value": "Forbidden"}]}]},
                    headers={"If-Match": tag},
                ),
            )
            assert publication.status_code == 200, publication.text
            assert edit.status_code in {409, 412}, edit.text
            after = await state(service.sessions, identifier)
            assert after.current == before.current
            assert after.state.public_revision == after.state.approved_revision == 2
            assert len(after.events) == len(before.events) + 1

    asyncio.run(scenario())


def test_http_live_keyset_pages_and_wrong_resource_events() -> None:
    async def scenario() -> None:
        async with client_context() as (client, _service):
            first, other = await seed(client), await seed(client)
            path, tag = first.headers["location"], first.headers["etag"]
            page = (await client.get(path + "/audit?limit=1")).json()
            event = page["items"][0]
            assert (
                await client.get(other.headers["location"] + "/audit/" + event["event_id"])
            ).status_code == 404
            cursor = page["next_cursor"]
            changed = await client.patch(path, json={}, headers={"If-Match": tag})
            assert changed.status_code == 200
            subsequent = await client.get(
                path + "/audit",
                params={
                    "after_time": cursor["occurred_at"],
                    "after_event_id": cursor["event_id"],
                    "limit": 1,
                },
            )
            assert subsequent.status_code == 200
            assert subsequent.json()["items"][0]["event_id"] != event["event_id"]
            revisions = (await client.get(path + "/revisions?after_number=1&limit=1")).json()
            assert revisions["items"][0]["revision"]["number"] == 2
            wrong = path.replace("works", "subjects")
            for endpoint in ("editorial", "revisions", "history", "audit", "public"):
                assert (await client.get(wrong + "/" + endpoint)).status_code == 404
            for endpoint in ("editorial/submit", "revisions/restore"):
                response = await client.post(
                    wrong + "/" + endpoint,
                    json={"selected_revision": 2}
                    if "editorial" in endpoint
                    else {"source_revision": 1},
                    headers={"If-Match": changed.headers["etag"]},
                )
                assert response.status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tag,status",
    [
        (None, 428),
        ('W/"weak"', 400),
        ("*", 400),
        ('"a", "b"', 400),
        ('"obsolete"', 412),
    ],
)
def test_http_editorial_and_restore_require_existing_if_match_contract(
    tag: str | None, status: int
) -> None:
    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await seed(client)
            path, identifier = (
                first.headers["location"],
                UUID(first.json()["internal_id"]),
            )
            before = await state(service.sessions, identifier)
            for suffix, body in [
                ("editorial/submit", {"selected_revision": 1}),
                ("revisions/restore", {"source_revision": 1}),
            ]:
                response = await client.post(
                    path + "/" + suffix,
                    json=body,
                    headers={"If-Match": tag} if tag else {},
                )
                assert response.status_code == status, response.text
            assert await state(service.sessions, identifier) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("compound", [False, True])
def test_http_history_reads_incomplete_legacy_without_rewriting_or_inventing_events(
    compound: bool,
) -> None:
    from datetime import UTC, datetime

    from libris.modules.bibliographic.domain.models import ResourceIdentity
    from libris.modules.bibliographic.infrastructure.persistence import SemanticResource
    from libris.modules.bibliographic.infrastructure.rdf import BF
    from libris.modules.bibliographic.infrastructure.validation import (
        INSTITUTIONAL_PROFILE_ID,
        PROFILE_ID,
    )
    from sqlalchemy import insert

    async def scenario() -> None:
        async with client_context() as (client, service):
            identity = ResourceIdentity.create("https://legacy49.example.invalid/resources")
            document = [{"@id": identity.uri, "@type": [str(BF.Work)]}]
            if compound:
                document.append(
                    {
                        "@id": "https://legacy49.example.invalid/instance",
                        "@type": [str(BF.Instance)],
                    }
                )
            async with service.sessions() as session, session.begin():
                await session.execute(
                    insert(SemanticResource).values(
                        id=identity.internal_id,
                        uri=identity.uri,
                        current_revision=1,
                    )
                )
                await session.execute(
                    insert(SemanticRevision).values(
                        resource_id=identity.internal_id,
                        number=1,
                        document=document,
                        created_at=datetime.now(UTC),
                        source="legacy",
                        process="legacy",
                        profile=PROFILE_ID if compound else INSTITUTIONAL_PROFILE_ID,
                    )
                )
            path = f"/api/v1/catalog/works/{identity.internal_id}"
            before = await state(service.sessions, identity.internal_id)
            assert before.state is None and not before.events
            for endpoint in (
                "history",
                "audit",
                "revisions",
                "revisions/1",
                "revisions/1/snapshot",
                "revisions/compare?previous=1&current=1",
            ):
                response = await client.get(path + "/" + endpoint)
                assert response.status_code == 200, response.text
            assert (await client.get(path + "/audit")).json()["items"] == []
            entry = (await client.get(path + "/revisions")).json()["items"][0]
            assert entry["revision"]["actor_id"] is None and not entry["content_event_recorded"]
            assert (await client.get(path + "/public")).status_code == 404
            assert await state(service.sessions, identity.internal_id) == before

    asyncio.run(scenario())


def test_http_trusted_transition_actor_and_exact_selected_revision() -> None:
    from dataclasses import replace

    from libris.modules.identity.domain.models import actor_identifier
    from libris.modules.identity.domain.permissions import Permission

    async def scenario() -> None:
        async with client_context() as (client, service):
            first = await seed(client)
            path, identifier = first.headers["location"], UUID(first.json()["internal_id"])
            human = actor()
            human = replace(
                human,
                subject="second-synthetic-actor",
                actor_id=actor_identifier(human.issuer, "second-synthetic-actor"),
                permissions=frozenset({Permission.EDITORIAL_SUBMIT}),
            )
            app = client._transport.app
            app.dependency_overrides[get_current_actor] = lambda: human
            # A single operation grant suffices; read/audit are independently denied.
            assert (await client.get(path + "/editorial")).status_code == 403
            submitted = await command(client, path, "submit", etag=first.headers["etag"])
            assert submitted.status_code == 200
            observed = await state(service.sessions, identifier)
            assert observed.events[-1].actor_id == human.actor_id
            assert observed.events[0].actor_id != human.actor_id
            app.dependency_overrides[get_current_actor] = actor
            approved = await command(client, path, "approve")
            assert approved.status_code == 200
            before = await state(service.sessions, identifier)
            rejected = await command(client, path, "publish", selected=2)
            assert (
                rejected.status_code == 422
                and rejected.json()["code"] == "editorial.approval_stale"
            )
            assert await state(service.sessions, identifier) == before
            assert (await client.get(path + "/public")).status_code == 404
            assert (await command(client, path, "publish")).status_code == 200

    asyncio.run(scenario())
