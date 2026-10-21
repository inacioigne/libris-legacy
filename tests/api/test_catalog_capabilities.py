"""M05.04: HTTP real sem serviços externos e preservação pontual de RDF."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.bibliographic.api.schemas.common import (
    ExternalReference,
    LocalReference,
)
from libris.modules.bibliographic.api.schemas.relationships import (
    ContributionLink,
    DirectLink,
    PublicationLink,
    RelationshipCreate,
)
from libris.modules.bibliographic.application.validation import validate_catalog
from libris.modules.bibliographic.domain.errors import CatalogError
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
)
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    new_graph,
    parse_document,
)
from libris.modules.bibliographic.infrastructure.relationship_graph import (
    change_relationship_graph,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    ShaclValidationError,
)
from libris.modules.identity.api.dependencies import get_current_actor
from libris.modules.identity.domain.permissions import Permission as P
from rdflib import BNode, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, RDF, SKOS
from test_catalogographic_http import actor

ROOT = "https://example.invalid/work"
TITLE = f'<{ROOT}> a <{BF.Work}>; <{BF.title}> [a <{BF.Title}>; <{BF.mainTitle}> "Test"].'
CORPUS = Path(__file__).parents[1] / "fixtures/bibliographic/institutional"
INSTITUTIONAL_CASES = json.loads((CORPUS / "manifest.json").read_text())["fixtures"]


@asynccontextmanager
async def client_for(
    *permissions: P, authenticated: bool = True, limit: int = 1048576
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(_env_file=None, app_env="test", rdf_max_document_bytes=limit))
    if authenticated:
        app.dependency_overrides[get_current_actor] = lambda: actor(*permissions)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


def rdf_payload(
    document: str = TITLE, release: str = INSTITUTIONAL_PROFILE_ID
) -> dict[str, object]:
    return {
        "kind": "rdf",
        "profile_release": release,
        "format": "turtle",
        "document": document,
        "root": ROOT,
    }


@pytest.mark.parametrize("case", INSTITUTIONAL_CASES, ids=lambda case: case["scenario"])
def test_institutional_corpus_through_http_validation(case: dict[str, object]) -> None:
    async def scenario() -> None:
        graph = parse_document((CORPUS / str(case["file"])).read_text(), "turtle")
        # Build the composed representation explicitly; never rewrite fixture snapshots.
        for canonical, inverse in [(BF.instanceOf, BF.hasInstance), (BF.itemOf, BF.hasItem)]:
            for child, parent in list(graph.subject_objects(canonical)):
                if isinstance(parent, URIRef):
                    graph.add((parent, inverse, child))
        root, collection = next(
            (root, collection)
            for kind, collection in [(BF.Work, "works"), (BF.Instance, "instances"),
                                     (BF.Item, "items"), (BF.Person, "agents"),
                                     (BF.Organization, "agents"), (SKOS.Concept, "subjects")]
            for root in graph.subjects(RDF.type, kind)
        )
        async with client_for(P.BIBLIOGRAPHIC_CREATE, P.AUTHORITY_MANAGE) as client:
            with patch("sqlalchemy.ext.asyncio.AsyncSession.execute", side_effect=AssertionError):
                response = await client.post(f"/api/v1/catalog/{collection}/validation", json={
                    "kind": "rdf", "context": "composed", "root": str(root),
                    "profile_release": AUTHORITY_PROFILE_ID
                    if collection in {"agents", "subjects"} else INSTITUTIONAL_PROFILE_ID,
                    "format": "turtle", "document": graph.serialize(format="turtle"),
                })
        assert response.status_code == 200, response.text
        assert response.json()["conforms"] is case["valid"], response.text
        assert response.json()["contract_version"] == "1.0"
        assert response.json()["request_id"] == response.headers["x-request-id"]
        if not case["valid"]:
            assert any(issue["severity"] == "Violation" for issue in response.json()["issues"])

    asyncio.run(scenario())


def test_rdf_prevalidation_same_engine_and_no_sessions() -> None:
    async def scenario() -> None:
        async with client_for(P.BIBLIOGRAPHIC_CREATE) as client:
            with patch(
                "sqlalchemy.ext.asyncio.AsyncSession.execute",
                side_effect=AssertionError("No DB"),
            ):
                for document in [TITLE, f"<{ROOT}> a <{BF.Work}>."]:
                    response = await client.post(
                        "/api/v1/catalog/works/validation", json=rdf_payload(document)
                    )
                    assert response.status_code == 200, response.text
                    result = response.json()
                    expected = validate_catalog(
                        parse_document(document, "turtle"), INSTITUTIONAL_PROFILE_ID
                    ).public_dict()
                    assert result | {"request_id": None} == expected | {"request_id": None}
                    assert result["contract_version"] == "1.0"
                    assert result["request_id"] == response.headers["x-request-id"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case,status,code",
    [
        ("unknown", 404, "profile.unknown"),
        ("incompatible", 422, "profile.incompatible"),
        ("remote", 400, "request.malformed"),
        ("format", 422, "schema.invalid"),
        ("spoof", 422, "schema.invalid"),
        ("dto", 422, "schema.invalid"),
    ],
)
def test_invalid_prevalidation(case: str, status: int, code: str) -> None:
    async def scenario() -> None:
        body = rdf_payload()
        if case == "unknown":
            body["profile_release"] = "urn:missing:release"
        elif case == "incompatible":
            body["profile_release"] = "urn:libris:profile:subject:2.0.0"
        elif case == "remote":
            body.update(
                format="json-ld",
                document=json.dumps({"@context": "https://example.invalid/context"}),
            )
        elif case == "format":
            body["format"] = "xml"
        elif case == "spoof":
            body["actor_id"] = "forged"
        else:
            body = {
                "kind": "dto",
                "profile_release": INSTITUTIONAL_PROFILE_ID,
                "resource": {"title": []},
            }
        async with client_for(P.BIBLIOGRAPHIC_CREATE) as client:
            response = await client.post("/api/v1/catalog/works/validation", json=body)
            assert response.status_code == status, response.text
            assert response.json()["code"] == code
            assert response.headers["content-type"] == "application/problem+json"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "path,method",
    [
        ("/profiles", "GET"),
        ("/profiles/release?release_id=urn:missing", "GET"),
        ("/works/validation", "POST"),
        (f"/works/{uuid4()}/relationships", "GET"),
        (f"/works/{uuid4()}/relationships", "PATCH"),
    ],
)
@pytest.mark.parametrize("authenticated,status", [(False, 401), (True, 403)])
def test_capabilities_deny_by_default(
    path: str, method: str, authenticated: bool, status: int
) -> None:
    async def scenario() -> None:
        async with client_for(authenticated=authenticated) as client:
            body = rdf_payload() if path.endswith("validation") else {"operations": []}
            response = await client.request(
                method,
                "/api/v1/catalog" + path,
                json=body,
                headers={
                    "If-Match": '"valid"',
                    "actor_id": "forged",
                    "permissions": "system:admin",
                },
            )
            assert response.status_code == status, response.text

    asyncio.run(scenario())


def test_profiles_are_fixed_read_only_and_no_filesystem_leaks() -> None:
    async def scenario() -> None:
        async with client_for(P.METADATA_REVISION_READ) as client:
            first = await client.get("/api/v1/catalog/profiles")
            assert first.status_code == 200, first.text
            assert first.json() == (await client.get("/api/v1/catalog/profiles")).json()
            entries = first.json()
            assert len(entries) == 15
            assert CONTROLLED_VOCABULARY_PROFILE_ID in {
                entry["profile"]["id"] for entry in entries
            }
            assert {entry["profile"]["status"] for entry in entries} == {"candidate", "draft"}
            for entry in entries:
                assert entry["configured"] is True
                assert entry["profile"]["status"] in {"candidate", "draft"}
                assert "approved" not in entry
                release = entry["profile"]["release_id"]
                single = await client.get(
                    "/api/v1/catalog/profiles/release", params={"release_id": release}
                )
                assert single.json() == entry
            for token in [
                ".ttl",
                ".json",
                "/home/",
                "validationShapes",
                "profile_file",
            ]:
                assert token not in first.text
            for method in ["POST", "PATCH", "DELETE"]:
                assert (
                    await client.request(method, "/api/v1/catalog/profiles", json={})
                ).status_code == 405
            assert (
                await client.get(
                    "/api/v1/catalog/profiles/release",
                    params={"release_id": "urn:absent"},
                )
            ).status_code == 404

    asyncio.run(scenario())


def test_failed_engine_is_not_semantic_nonconformity() -> None:
    async def scenario() -> None:
        async with client_for(P.BIBLIOGRAPHIC_CREATE) as client:
            with patch(
                "libris.modules.bibliographic.application.validation.validate_profile",
                side_effect=ShaclValidationError("engine_failure", "private failure"),
            ):
                response = await client.post("/api/v1/catalog/works/validation", json=rdf_payload())
            assert response.status_code == 200
            assert response.json()["state"] == "failed"
            assert "conforms" not in response.json()
            assert response.json()["issues"][0]["code"] == "validator.failure"
            assert "private failure" not in response.text

    asyncio.run(scenario())


@pytest.mark.parametrize("body", ["x" * 2048, json.dumps({"x": [[[[1]]]]})])
def test_bounded_input(body: str) -> None:
    async def scenario() -> None:
        async with client_for(P.BIBLIOGRAPHIC_CREATE, limit=100) as client:
            response = await client.post(
                "/api/v1/catalog/works/validation",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code in {400, 422}

    asyncio.run(scenario())


def test_deep_json_rejected_before_fastapi() -> None:
    async def scenario() -> None:
        async with client_for(P.BIBLIOGRAPHIC_CREATE) as client:
            response = await client.post(
                "/api/v1/catalog/works/validation",
                content="[" * 66 + "0" + "]" * 66,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 400

    asyncio.run(scenario())


def test_relation_graph_preserves_all_unaffected_terms() -> None:
    identity = ResourceIdentity.create("https://example.invalid/resources")
    source = LocalReference(
        entity_type=EntityType.WORK, internal_id=identity.internal_id, uri=identity.uri
    )
    target = ExternalReference(entity_type=EntityType.WORK, uri="urn:work:external")
    link = DirectLink(source=source, target=target, predicate=str(BF.relatedTo))
    graph = parse_document(TITLE.replace(ROOT, identity.uri), "turtle")
    extension = (
        URIRef(identity.uri),
        DCTERMS.abstract,
        Literal("Preservar", lang="pt-BR"),
    )
    graph.add(extension)
    original = set(graph)
    before = RelationshipCreate(source=source)
    after = RelationshipCreate(source=source, links=[link])
    added = change_relationship_graph(graph, before, after)
    assert set(added) - original == {(URIRef(identity.uri), BF.relatedTo, URIRef(target.uri))}
    assert set(graph) == original
    assert isomorphic(change_relationship_graph(added, after, before), graph)


def test_publication_changes_only_requested_agent_and_contribution_annotations_refuse_loss() -> (
    None
):
    source = LocalReference(entity_type=EntityType.INSTANCE, internal_id=uuid4(), uri=ROOT)
    work = DirectLink(
        source=source,
        predicate=str(BF.instanceOf),
        target=ExternalReference(entity_type=EntityType.WORK, uri="urn:work:1"),
    )
    agent = ExternalReference(entity_type=EntityType.AGENT, uri="urn:agent:1")
    link = PublicationLink(source=source, target=agent, activity_key="publication")
    graph = new_graph()
    node = BNode("publication")
    graph.add((URIRef(ROOT), BF.provisionActivity, node))
    graph.add((node, RDF.type, BF.Publication))
    graph.add((node, BF.agent, URIRef(agent.uri)))
    graph.add((node, BF.date, Literal("uncertain")))
    changed = change_relationship_graph(
        graph,
        RelationshipCreate(source=source, links=[work, link]),
        RelationshipCreate(source=source, links=[work]),
    )
    assert set(graph) - set(changed) == {(node, BF.agent, URIRef(agent.uri))}
    source = source.model_copy(update={"entity_type": EntityType.WORK})
    contribution = ContributionLink(source=source, target=agent, role=["urn:role:author"])
    graph = new_graph()
    graph.add((URIRef(ROOT), BF.contribution, node))
    graph.add((node, RDF.type, BF.Contribution))
    graph.add((node, BF.agent, URIRef(agent.uri)))
    graph.add((node, BF.role, URIRef("urn:role:author")))
    graph.add((node, DCTERMS.description, Literal("annotation")))
    before = set(graph)
    with pytest.raises(CatalogError):
        change_relationship_graph(
            graph,
            RelationshipCreate(source=source, links=[contribution]),
            RelationshipCreate(source=source),
        )
    assert set(graph) == before


def test_profile_policy_draft_selection_and_jsonld_composed() -> None:
    async def scenario() -> None:
        async with client_for(P.BIBLIOGRAPHIC_CREATE) as client:
            draft = await client.post(
                "/api/v1/catalog/works/validation",
                json=rdf_payload(release="urn:libris:profile:work-minimal:2.0.0"),
            )
            assert draft.status_code == 422 and draft.json()["code"] == "profile.incompatible"
            graph = parse_document(TITLE, "turtle")
            graph.add((URIRef(ROOT), DCTERMS.abstract, Literal("first", lang="pt")))
            graph.add((URIRef(ROOT), DCTERMS.abstract, Literal("second", lang="pt")))
            extended = rdf_payload(
                graph.serialize(format="turtle"), "urn:libris:profile:work-extensions:2.0.0"
            )
            policy = await client.post("/api/v1/catalog/works/validation", json=extended)
            assert policy.status_code == 200 and policy.json()["conforms"] is False
            assert any(
                issue["path"].get("iri") == str(DCTERMS.abstract)
                for issue in policy.json()["issues"]
                if "path" in issue
            )
            jsonld = rdf_payload()
            jsonld.update(
                format="json-ld",
                document=parse_document(TITLE, "turtle").serialize(format="json-ld"),
            )
            valid = await client.post("/api/v1/catalog/works/validation", json=jsonld)
            assert valid.status_code == 200 and valid.json()["conforms"] is True
            composed = await client.post(
                "/api/v1/catalog/works/validation", json=rdf_payload() | {"context": "composed"}
            )
            assert composed.status_code == 200 and composed.json()["conforms"] is True
            duplicate = await client.post(
                "/api/v1/catalog/works/validation",
                content='{"kind":"rdf","kind":"dto"}',
                headers={"Content-Type": "application/json"},
            )
            assert duplicate.status_code == 400

    asyncio.run(scenario())
