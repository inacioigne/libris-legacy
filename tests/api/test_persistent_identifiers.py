"""Implemented identity contracts; no simulated lifecycle or import persistence."""

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from libris.core.config import Settings
from libris.modules.bibliographic.api import schemas
from libris.modules.bibliographic.api.schemas.common import (
    CompatibleReference,
    EntityReference,
    ExternalReference,
    LocalReference,
    UnreconciledReference,
)
from libris.modules.bibliographic.api.schemas.relationships import RelationshipCreate
from libris.modules.bibliographic.application.relationships import (
    ResolvedEntity,
    validate_references,
)
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.infrastructure.ownership import owned_nodes
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    new_graph,
    parse_document,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    validate_profile,
)
from pydantic import TypeAdapter, ValidationError
from rdflib import URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, RDF, SKOS

ROOT = Path(__file__).parents[1] / "fixtures/bibliographic/identifiers"
IDENTIFIER = UUID("00000000-0000-4000-8000-000000000001")
URI = f"https://example.org/resources/{IDENTIFIER}"


@pytest.mark.parametrize(
    "uri",
    [
        URI,
        URI.replace("https:", "http:"),
        "https://[::1]:8000/resources/1",
        "https://example.org/resources/%C3%A7",
    ],
)
def test_domain_accepts_absolute_identity_without_rewriting(uri: str) -> None:
    assert ResourceIdentity(IDENTIFIER, uri).uri == uri


@pytest.mark.parametrize(
    "uri",
    [
        "urn:test:1",
        "/resources/1",
        "https://",
        "https://:80/resources",
        URI + "?query=1",
        URI + "?",
        URI + "#part",
        URI + "#",
        "https://user:password@example.org/resources/1",
        "https://@example.org/resources/1",
        "https://example.org:bad/resources/1",
        "https://example.org:65536/resources/1",
        URI + " space",
        URI + "\t",
        URI + "\n",
        URI + "\x00",
        URI + "<",
        URI + "%zz",
        URI + "%",
    ],
)
def test_domain_rejects_invalid_identity_and_base(uri: str) -> None:
    with pytest.raises(ValueError):
        ResourceIdentity(IDENTIFIER, uri)
    with pytest.raises(ValueError):
        ResourceIdentity.create(uri, IDENTIFIER)


def test_uuid_type_and_immutable_identity() -> None:
    with pytest.raises(ValueError, match="UUID"):
        ResourceIdentity("not-a-uuid", URI)  # type: ignore[arg-type]
    identity = ResourceIdentity.create("https://example.org/resources/", IDENTIFIER)
    assert identity.internal_id == IDENTIFIER
    assert identity.uri == URI
    with pytest.raises(FrozenInstanceError):
        identity.uri = "https://changed.example.org/resources/1"
    generated = ResourceIdentity.create("https://example.org/resources")
    assert generated.internal_id.version == 4


def test_configuration_change_only_affects_new_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESOURCE_BASE_URI", "https://example.org/resources")
    stored = ResourceIdentity.create(str(Settings(_env_file=None).resource_base_uri), IDENTIFIER)
    monkeypatch.setenv("RESOURCE_BASE_URI", "https://changed.example.org/resources")
    new = ResourceIdentity.create(str(Settings(_env_file=None).resource_base_uri))
    assert new.uri.startswith("https://changed.example.org/resources/")
    assert stored.uri == URI
    # Recovery uses the stored pair, including a historical base.
    assert ResourceIdentity(stored.internal_id, stored.uri) == stored


@pytest.mark.parametrize("kind", ["Work", "Instance", "Item", "Agent", "Subject"])
def test_read_fixtures_and_metadata_updates_preserve_identity(kind: str) -> None:
    read = getattr(schemas, kind + "Read")
    data = json.loads((ROOT / f"{kind.lower()}-read.json").read_text())
    model = read.model_validate(data)
    assert read.model_validate_json(model.model_dump_json()) == model
    identity = ResourceIdentity(model.internal_id, model.uri)
    # A new description keeps its supplied identity; service/database revision
    # stability is covered by the separately executed PostgreSQL suite.
    fields = {"Work": "title", "Agent": "authorized_name", "Subject": "preferred_label"}
    if kind in fields:
        field = fields[kind]
        if kind == "Work":
            data[field][0]["main_title"][0]["value"] = "Título corrigido"
        else:
            data[field][0]["value"] = "Rótulo corrigido"
    elif kind == "Instance":
        data["identified_by"] = [{"kind": "Isbn", "value": "outro valor transcrito"}]
    else:
        data["shelf_mark"] = [{"value": "Nova localização bibliográfica"}]
    changed = read.model_validate(data)
    assert ResourceIdentity(changed.internal_id, changed.uri) == identity


@pytest.mark.parametrize("index", range(6))
def test_reference_examples_are_executable(index: int) -> None:
    data = json.loads((ROOT / "references.json").read_text())[index]
    reference = TypeAdapter(CompatibleReference).validate_python(data)
    assert TypeAdapter(CompatibleReference).validate_json(reference.model_dump_json()) == reference


class StoredResolver:
    """Explicit in-memory double, independent of the configured current base."""

    def __init__(self, entity: ResolvedEntity | None) -> None:
        self.entity = entity

    async def resolve(self, reference: LocalReference) -> ResolvedEntity | None:
        return self.entity


@pytest.mark.parametrize(
    "fields",
    [
        {"internal_id": IDENTIFIER},
        {"uri": URI},
        {"internal_id": IDENTIFIER, "uri": URI},
    ],
)
def test_local_resolution_uses_stored_identity(fields: dict[str, object]) -> None:
    source = LocalReference.model_validate({"entity_type": "Work", **fields})
    state = RelationshipCreate(source=source)
    report = asyncio.run(
        validate_references(
            state,
            StoredResolver(ResolvedEntity(ResourceIdentity(IDENTIFIER, URI), EntityType.WORK)),
        )
    )
    assert report.pending == ()


@pytest.mark.parametrize(
    "entity",
    [
        None,
        ResolvedEntity(ResourceIdentity(IDENTIFIER, URI), EntityType.ITEM),
        ResolvedEntity(ResourceIdentity(UUID(int=2), URI), EntityType.WORK),
        ResolvedEntity(
            ResourceIdentity(IDENTIFIER, "https://old.example.org/resources/1"), EntityType.WORK
        ),
    ],
)
def test_nonexistent_wrong_type_or_inconsistent_stored_identity(
    entity: ResolvedEntity | None,
) -> None:
    source = LocalReference(entity_type=EntityType.WORK, internal_id=IDENTIFIER, uri=URI)
    with pytest.raises(ValueError):
        asyncio.run(validate_references(RelationshipCreate(source=source), StoredResolver(entity)))


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "local", "entity_type": "Work"},
        {"kind": "local", "entity_type": "Work", "internal_id": "bad"},
        {
            "kind": "external",
            "entity_type": "Agent",
            "uri": "https://viaf.org/viaf/123",
            "external_id": "123",
        },
        {
            "kind": "unreconciled",
            "entity_type": "Agent",
            "label": " ",
            "namespace": "urn:test:source",
            "key": "123",
        },
        {
            "kind": "unreconciled",
            "entity_type": "Agent",
            "label": "Nome",
            "namespace": "urn:test:source",
            "key": "",
        },
        {"kind": "unreconciled", "entity_type": "Agent", "label": "Nome", "key": "123"},
    ],
)
def test_invalid_reference_contracts(data: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(EntityReference).validate_python(data)


@pytest.mark.parametrize(
    "pending",
    [
        ExternalReference(entity_type=EntityType.SUBJECT, uri="https://example.org/concept/1"),
        UnreconciledReference(
            entity_type=EntityType.SUBJECT,
            label="Termo",
            namespace="urn:test:source",
            key="record-1:subject",
        ),
    ],
)
def test_pending_references_do_not_resolve_over_http(
    pending: ExternalReference | UnreconciledReference,
) -> None:
    source = LocalReference(entity_type=EntityType.WORK, internal_id=IDENTIFIER)
    collection = RelationshipCreate.model_validate(
        {
            "source": source.model_dump(),
            "links": [
                {
                    "kind": "direct",
                    "source": source.model_dump(),
                    "predicate": str(BF.subject),
                    "target": pending.model_dump(),
                }
            ],
        }
    )
    resolver = StoredResolver(ResolvedEntity(ResourceIdentity(IDENTIFIER, URI), EntityType.WORK))
    if isinstance(pending, UnreconciledReference):
        with pytest.raises(ValueError, match="rascunho"):
            asyncio.run(validate_references(collection, resolver))
    assert asyncio.run(
        validate_references(collection, resolver, allow_unreconciled=True)
    ).pending == (pending,)


def test_shared_external_identifier_is_not_local_identity() -> None:
    data = json.loads((ROOT / "instance-read.json").read_text())
    first = schemas.InstanceRead.model_validate(data)
    data["internal_id"] = "00000000-0000-4000-8000-000000000098"
    data["uri"] = "https://example.org/resources/" + data["internal_id"]
    second = schemas.InstanceRead.model_validate(data)
    assert first.identified_by == second.identified_by
    assert first.internal_id != second.internal_id


def test_jsonld_and_turtle_examples_are_isomorphic_and_profile_valid() -> None:
    graph = parse_document((ROOT / "entities.jsonld").read_text(), "json-ld")
    turtle = parse_document((ROOT / "entities.ttl").read_text(), "turtle")
    assert isomorphic(graph, turtle)
    assert (
        URIRef(URI.replace(str(IDENTIFIER), "00000000-0000-4000-8000-000000000002")),
        BF.instanceOf,
        URIRef(URI),
    ) in graph
    assert not list(graph.triples((None, OWL.sameAs, None)))
    assert len(list(graph.subjects(RDF.type, BF.Contribution))) == 1
    for kind in (BF.Work, BF.Instance, BF.Item, BF.Person, SKOS.Concept):
        root = next(graph.subjects(RDF.type, kind))
        assert isinstance(root, URIRef)
        snapshot = new_graph()
        nodes = owned_nodes(graph, root)
        for triple in graph:
            if triple[0] in nodes:
                snapshot.add(triple)
        profile = (
            AUTHORITY_PROFILE_ID if kind in (BF.Person, SKOS.Concept) else INSTITUTIONAL_PROFILE_ID
        )
        assert validate_profile(snapshot, profile).conforms


def test_fastapi_schema_exposes_discriminated_references_without_endpoints() -> None:
    isolated = FastAPI()

    @isolated.post("/example")
    def example(body: schemas.InstanceCreate) -> schemas.InstanceCreate:
        return body

    contract = isolated.openapi()
    components = contract["components"]["schemas"]
    assert {"LocalReference", "ExternalReference", "UnreconciledReference"} <= components.keys()
    assert components["LocalReference"]["properties"]["internal_id"]["anyOf"][0]["format"] == "uuid"
