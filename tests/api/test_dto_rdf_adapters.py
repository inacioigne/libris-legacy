"""M05.02: full projections and conservative semantic patches, offline."""

import socket
from pathlib import Path
from uuid import UUID

import pytest
from libris.modules.bibliographic.api.schemas import (
    AgentCreate,
    AgentRead,
    AgentUpdate,
    InstanceCreate,
    InstanceRead,
    InstanceUpdate,
    ItemCreate,
    ItemRead,
    ItemUpdate,
    SubjectCreate,
    SubjectRead,
    SubjectUpdate,
    WorkCreate,
    WorkRead,
    WorkUpdate,
)
from libris.modules.bibliographic.application.diagnostics import (
    CatalogValidationRejected,
)
from libris.modules.bibliographic.application.relationships import ResolvedEntity
from libris.modules.bibliographic.domain.errors import CatalogError
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.infrastructure.profiles import load_profile_registry
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    RdfDocumentError,
    parse_document,
)
from libris.modules.bibliographic.infrastructure.serialization import (
    ResourceCreate,
    ResourceRead,
    ResourceUpdate,
    apply_update,
    create_to_graph,
    graph_to_read,
    parse_representation,
    resource_to_graph,
    serialize_graph,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
)
from pydantic import ValidationError
from rdflib import BNode, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, PROV, RDF, RDFS, XSD

FIXTURES = Path(__file__).parents[1] / "fixtures/bibliographic/serialization/valid"
CASES = [
    ("work", WorkCreate, WorkRead, WorkUpdate, INSTITUTIONAL_PROFILE_ID),
    (
        "instance",
        InstanceCreate,
        InstanceRead,
        InstanceUpdate,
        INSTITUTIONAL_PROFILE_ID,
    ),
    ("item", ItemCreate, ItemRead, ItemUpdate, INSTITUTIONAL_PROFILE_ID),
    ("agent", AgentCreate, AgentRead, AgentUpdate, AUTHORITY_PROFILE_ID),
    ("subject", SubjectCreate, SubjectRead, SubjectUpdate, AUTHORITY_PROFILE_ID),
]


def resource(name: str = "work") -> ResourceRead:
    model = next(case[2] for case in CASES if case[0] == name)
    return model.model_validate_json((FIXTURES / f"{name}.json").read_text())


def identity(dto: ResourceRead) -> ResourceIdentity:
    return ResourceIdentity(dto.internal_id, dto.uri)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Mapper tentou consultar rede")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)


@pytest.mark.parametrize("name,create,read,update,profile", CASES)
def test_complete_roundtrips(
    name: str,
    create: type[ResourceCreate],
    read: type[ResourceRead],
    update: type[ResourceUpdate],
    profile: str,
) -> None:
    original = resource(name)
    stored = identity(original)
    payload = original.model_dump(exclude={"internal_id", "uri"})
    graph = create_to_graph(create.model_validate(payload), stored, profile=profile)
    assert isomorphic(graph, resource_to_graph(original))
    restored = graph_to_read(graph, stored)
    assert type(restored) is read
    assert restored.internal_id == stored.internal_id and restored.uri == stored.uri
    rebuilt = resource_to_graph(restored)
    assert isomorphic(graph, rebuilt)
    assert graph_to_read(rebuilt, stored) == restored
    before = set(graph)
    namespaces = tuple(graph.namespaces())
    assert isomorphic(graph, apply_update(graph, update(), stored, profile=profile))
    assert set(graph) == before and tuple(graph.namespaces()) == namespaces
    for format in ("json-ld", "turtle"):
        parsed = parse_representation(serialize_graph(graph, format=format), format)
        assert isomorphic(graph, resource_to_graph(graph_to_read(parsed, stored)))
    expanded = parse_document(serialize_graph(graph, format="json-ld"), "json-ld")
    assert isomorphic(graph, expanded)


@pytest.mark.parametrize("name,create,read,update,profile", CASES)
def test_each_public_field_is_replaced_independently(
    name: str,
    create: type[ResourceCreate],
    read: type[ResourceRead],
    update: type[ResourceUpdate],
    profile: str,
) -> None:
    dto = resource(name)
    graph = resource_to_graph(dto)
    for field in update.model_fields:
        patch = update.model_validate({field: getattr(dto, field)})
        result = apply_update(graph, patch, identity(dto), profile=profile)
        assert isomorphic(graph, result), field


@pytest.mark.parametrize("name,create,read,update,profile", CASES)
def test_null_empty_and_omitted_fields(
    name: str,
    create: type[ResourceCreate],
    read: type[ResourceRead],
    update: type[ResourceUpdate],
    profile: str,
) -> None:
    dto = resource(name)
    graph = resource_to_graph(dto)
    for field in update.model_fields:
        if field in update.required_fields:
            with pytest.raises(ValidationError):
                update.model_validate({field: None})
            with pytest.raises(ValidationError):
                update.model_validate({field: []})
        else:
            cleared = []
            for value in (None, []):
                result = apply_update(
                    graph,
                    update.model_validate({field: value}),
                    identity(dto),
                    profile=profile,
                )
                projected = graph_to_read(result, identity(dto))
                assert getattr(projected, field) == []
                cleared.append(result)
                for other in update.model_fields:
                    if other != field:
                        assert getattr(projected, other) == getattr(
                            graph_to_read(graph, identity(dto)), other
                        )
            assert isomorphic(*cleared)


def test_languages_lexical_dates_and_grouping() -> None:
    dto = resource("instance")
    raw = dto.model_dump() | {
        "provision_activity": [
            {
                "agent": [{"uri": "urn:publisher"}],
                "place": [{"uri": "urn:place"}],
                "date": [
                    {"value": "0024", "datatype": str(XSD.gYear)},
                    {"value": "2024-02-29", "datatype": str(XSD.date)},
                    {"value": "[2024?]", "datatype": str(XSD.string)},
                ],
            }
        ]
    }
    dto = InstanceRead.model_validate(raw)
    graph = resource_to_graph(dto)
    restored = graph_to_read(graph, identity(dto))
    assert isinstance(restored, InstanceRead)
    assert {(d.value, d.datatype) for d in restored.provision_activity[0].date} == {
        (d.value, d.datatype) for d in dto.provision_activity[0].date
    }
    assert isomorphic(graph, resource_to_graph(restored))
    title = WorkRead.model_validate(
        resource().model_dump()
        | {
            "title": [
                {
                    "main_title": [
                        {"value": "Sem idioma"},
                        {"value": "Português", "language": "pt-BR"},
                        {"value": "English", "language": "en"},
                    ]
                }
            ]
        }
    )
    restored_title = graph_to_read(resource_to_graph(title), identity(title))
    assert isinstance(restored_title, WorkRead)
    assert {t.language for t in restored_title.title[0].main_title} == {
        None,
        "pt-BR",
        "en",
    }


def test_extensions_and_historical_unknown_auxiliaries_survive() -> None:
    dto = resource()
    graph = resource_to_graph(dto)
    root = URIRef(dto.uri)
    extension = BNode()
    graph.add((root, DCTERMS.abstract, Literal("Resumo", lang="pt-BR")))
    graph.add((root, PROV.wasDerivedFrom, extension))
    graph.add((extension, RDF.type, PROV.Entity))
    graph.add((extension, RDFS.label, Literal("Fonte", datatype=XSD.string)))
    before = set(graph)
    profile = load_profile_registry().resolve(
        "urn:libris:profile:work-extensions:2.0.0"
    )
    patched = apply_update(
        graph,
        WorkUpdate(title=[{"main_title": [{"value": "Novo"}]}]),
        identity(dto),
        profile=profile,
    )
    assert (root, DCTERMS.abstract, Literal("Resumo", lang="pt-BR")) in patched
    assert set(graph.triples((extension, None, None))) <= set(patched)
    assert set(graph) == before
    assert len(resource_to_graph(graph_to_read(patched, identity(dto)))) < len(patched)
    for format in ("json-ld", "turtle"):
        assert isomorphic(
            patched,
            parse_representation(serialize_graph(patched, format=format), format),
        )
    # A release that cannot edit the extension still preserves it on independent edits.
    assert (root, DCTERMS.abstract, Literal("Resumo", lang="pt-BR")) in apply_update(
        graph,
        WorkUpdate(note=None),
        identity(dto),
        profile=INSTITUTIONAL_PROFILE_ID,
    )


def test_private_fragment_auxiliary_and_shared_nodes() -> None:
    dto = resource()
    graph = resource_to_graph(dto)
    root = URIRef(dto.uri)
    old = next(graph.objects(root, BF.title))
    private = URIRef(dto.uri + "#stored-title")
    for s, p, o in list(graph):
        if s == old or o == old:
            graph.remove((s, p, o))
            graph.add((private if s == old else s, p, private if o == old else o))
    graph.add((root, URIRef("urn:historical:shared"), private))
    result = apply_update(
        graph,
        WorkUpdate(title=[{"main_title": [{"value": "Novo"}]}]),
        identity(dto),
        profile=INSTITUTIONAL_PROFILE_ID,
    )
    assert set(graph.triples((private, None, None))) <= set(result)
    assert (root, URIRef("urn:historical:shared"), private) in result
    assert (root, BF.title, private) not in result


@pytest.mark.parametrize(
    ("name", "predicate", "update"),
    [
        ("work", BF.title, WorkUpdate(title=[{"main_title": [{"value": "Replacement"}]}])),
        ("instance", BF.extent, InstanceUpdate(extent=[])),
    ],
)
def test_named_auxiliary_replacement_rejected_without_losing_its_uri(
    name: str, predicate: URIRef, update: ResourceUpdate
) -> None:
    dto = resource(name)
    graph = resource_to_graph(dto)
    root = URIRef(dto.uri)
    original = next(graph.objects(root, predicate))
    named = URIRef(dto.uri + "#persistent-title")
    for subject, property_uri, value in list(graph):
        if subject == original or value == original:
            graph.remove((subject, property_uri, value))
            graph.add((named if subject == original else subject, property_uri,
                       named if value == original else value))
    before = set(graph)
    with pytest.raises(RdfDocumentError, match="URI"):
        apply_update(
            graph,
            update,
            identity(dto),
            profile=INSTITUTIONAL_PROFILE_ID,
        )
    assert set(graph) == before
    preserved = apply_update(
        graph, WorkUpdate(note=[]) if name == "work" else InstanceUpdate(note=[]),
        identity(dto), profile=INSTITUTIONAL_PROFILE_ID
    )
    assert (root, predicate, named) in preserved
    assert set(graph.triples((named, None, None))) <= set(preserved)


def test_annotated_auxiliary_replacement_rejected_without_mutation() -> None:
    dto = resource()
    graph = resource_to_graph(dto)
    title = next(graph.objects(URIRef(dto.uri), BF.title))
    graph.add((title, DCTERMS.abstract, Literal("Annotation", lang="en")))
    before = set(graph)
    with pytest.raises(RdfDocumentError, match="apagaria"):
        apply_update(
            graph,
            WorkUpdate(title=[{"main_title": [{"value": "Novo"}]}]),
            identity(dto),
            profile=INSTITUTIONAL_PROFILE_ID,
        )
    assert set(graph) == before
    assert (
        isomorphic(
            graph,
            apply_update(
                graph,
                WorkUpdate(language=[]),
                identity(dto),
                profile=INSTITUTIONAL_PROFILE_ID,
            ),
        )
        is False
    )
    assert set(graph) == before


def test_references_use_existing_resolution_and_canonical_uri_projection() -> None:
    dto = resource()
    subject = ResolvedEntity(
        ResourceIdentity(UUID(int=7), "https://example.org/subjects/stored"),
        EntityType.SUBJECT,
    )
    reference = {
        "kind": "local",
        "entity_type": "Subject",
        "internal_id": subject.identity.internal_id,
    }
    mapping = {subject.identity.internal_id: subject, subject.identity.uri: subject}
    graph = apply_update(
        resource_to_graph(dto),
        WorkUpdate(subject=[reference]),
        identity(dto),
        profile=INSTITUTIONAL_PROFILE_ID,
        references=mapping,
    )
    restored = graph_to_read(graph, identity(dto))
    assert isinstance(restored, WorkRead)
    assert restored.subject[0].model_dump() == {"uri": subject.identity.uri}
    with pytest.raises(RdfDocumentError):
        apply_update(
            graph,
            WorkUpdate(subject=[reference | {"uri": "https://example.org/wrong"}]),
            identity(dto),
            profile=INSTITUTIONAL_PROFILE_ID,
            references=mapping,
        )
    with pytest.raises(RdfDocumentError):
        apply_update(
            graph,
            WorkUpdate(subject=[reference]),
            identity(dto),
            profile=INSTITUTIONAL_PROFILE_ID,
        )
    with pytest.raises(RdfDocumentError):
        apply_update(
            graph,
            WorkUpdate(related_to=[{"uri": dto.uri}]),
            identity(dto),
            profile=INSTITUTIONAL_PROFILE_ID,
        )


@pytest.mark.parametrize("name,create,read,update,profile", CASES)
def test_unreconciled_extra_identity_and_wrong_update_rejected(
    name: str,
    create: type[ResourceCreate],
    read: type[ResourceRead],
    update: type[ResourceUpdate],
    profile: str,
) -> None:
    dto = resource(name)
    graph = resource_to_graph(dto)
    before = set(graph)
    with pytest.raises(ValidationError):
        update.model_validate({"uri": "https://example.org/other"})
    with pytest.raises(ValidationError):
        update.model_validate({"abstract": "unauthorized"})
    wrong = AgentUpdate() if name != "agent" else WorkUpdate()
    with pytest.raises(RdfDocumentError):
        apply_update(graph, wrong, identity(dto), profile=profile)
    with pytest.raises(ValueError):
        graph_to_read(
            graph, ResourceIdentity(dto.internal_id, "https://example.org/wrong")
        )
    with pytest.raises(RdfDocumentError):
        create_to_graph(
            create.model_validate(dto.model_dump(exclude={"internal_id", "uri"})),
            identity(dto),
            profile=profile,
            references={
                dto.uri: ResolvedEntity(
                    ResourceIdentity(UUID(int=100), dto.uri), EntityType.WORK
                )
            },
        )
    assert set(graph) == before


@pytest.mark.parametrize(
    "mutation", ["cardinality", "literal", "datatype", "langString", "class", "foreign"]
)
def test_malformed_graph_projection_rejects(mutation: str) -> None:
    dto = resource("instance")
    graph = resource_to_graph(dto)
    root = URIRef(dto.uri)
    if mutation == "cardinality":
        graph.add((root, BF.instanceOf, URIRef("urn:second")))
    elif mutation == "literal":
        graph.add((root, BF.carrier, Literal("not an IRI")))
    elif mutation == "datatype":
        graph.add((root, BF.editionStatement, Literal("5", datatype=XSD.integer)))
    elif mutation == "langString":
        graph.add(
            (root, BF.editionStatement, Literal("missing tag", datatype=RDF.langString))
        )
    elif mutation == "class":
        node = BNode()
        graph.add((root, BF.extent, node))
        graph.add((node, RDFS.label, Literal("untyped")))
    else:
        graph.add(
            (URIRef("https://example.org/foreign"), RDFS.label, Literal("foreign"))
        )
    before = set(graph)
    with pytest.raises(ValueError):
        graph_to_read(graph, identity(dto))
    assert set(graph) == before


def test_combined_state_validation_and_shacl_failure_are_atomic() -> None:
    dto = resource("subject")
    graph = resource_to_graph(dto)
    before = set(graph)
    with pytest.raises(ValidationError):
        apply_update(
            graph,
            SubjectUpdate(alternative_label=dto.preferred_label),
            identity(dto),
            profile=AUTHORITY_PROFILE_ID,
        )
    assert set(graph) == before
    work = resource()
    graph = resource_to_graph(work)
    graph.add((URIRef(work.uri), DCTERMS.abstract, Literal("1", lang="en")))
    graph.add((URIRef(work.uri), DCTERMS.abstract, Literal("2", lang="en")))
    before = set(graph)
    profile = load_profile_registry().resolve(
        "urn:libris:profile:work-extensions:2.0.0"
    )
    with pytest.raises(CatalogValidationRejected):
        apply_update(graph, WorkUpdate(), identity(work), profile=profile)
    assert set(graph) == before
    with pytest.raises(CatalogError):
        create_to_graph(
            WorkCreate(title=[{"main_title": [{"value": "Title"}]}]),
            identity(work),
            profile=AUTHORITY_PROFILE_ID,
        )


def test_unreconciled_references_and_duplicates() -> None:
    dto = resource()
    reference = {
        "kind": "unreconciled",
        "entity_type": "Subject",
        "label": "Pending",
        "namespace": "urn:source:",
        "key": "1",
    }
    with pytest.raises(RdfDocumentError):
        apply_update(
            resource_to_graph(dto),
            WorkUpdate(subject=[reference]),
            identity(dto),
            profile=INSTITUTIONAL_PROFILE_ID,
        )
    duplicate = WorkRead.model_validate(
        dto.model_dump() | {"language": [{"uri": "urn:lang"}] * 2}
    )
    restored = graph_to_read(resource_to_graph(duplicate), identity(dto))
    assert isinstance(restored, WorkRead) and len(restored.language) == 1


@pytest.mark.parametrize("kind", ["Agent", "Person", "Organization"])
def test_agent_subtypes(kind: str) -> None:
    dto = AgentRead.model_validate(resource("agent").model_dump() | {"kind": kind})
    graph = create_to_graph(
        AgentCreate.model_validate(dto.model_dump(exclude={"internal_id", "uri"})),
        identity(dto),
        profile=AUTHORITY_PROFILE_ID,
    )
    read = graph_to_read(graph, identity(dto))
    assert isinstance(read, AgentRead) and read.kind == kind
    assert isomorphic(graph, resource_to_graph(read))
    with pytest.raises(ValidationError):
        AgentUpdate.model_validate({"kind": "Organization"})


def test_resolved_reference_type_and_holder_subtype() -> None:
    dto = resource("item")
    stored = ResourceIdentity(UUID(int=77), "https://example.org/holders/77")
    patch = ItemUpdate(
        held_by=[
            {"kind": "local", "entity_type": "Agent", "internal_id": stored.internal_id}
        ]
    )
    for entity in (
        ResolvedEntity(stored, EntityType.WORK),
        ResolvedEntity(stored, EntityType.AGENT, "Person"),
    ):
        with pytest.raises(RdfDocumentError):
            apply_update(
                resource_to_graph(dto),
                patch,
                identity(dto),
                profile=INSTITUTIONAL_PROFILE_ID,
                references={stored.internal_id: entity, stored.uri: entity},
            )
    entity = ResolvedEntity(stored, EntityType.AGENT, "Organization")
    graph = apply_update(
        resource_to_graph(dto),
        patch,
        identity(dto),
        profile=INSTITUTIONAL_PROFILE_ID,
        references={stored.internal_id: entity, stored.uri: entity},
    )
    assert (URIRef(dto.uri), BF.heldBy, URIRef(stored.uri)) in graph


def test_explicit_string_datatype_is_a_projection_limit() -> None:
    dto = resource("agent")
    graph = resource_to_graph(dto)
    root = URIRef(dto.uri)
    graph.add((root, RDFS.label, Literal("Explicit", datatype=XSD.string)))
    before = set(graph)
    projected = graph_to_read(graph, identity(dto))
    assert not isomorphic(graph, resource_to_graph(projected))
    result = apply_update(
        graph,
        AgentUpdate(variant_name=None),
        identity(dto),
        profile=AUTHORITY_PROFILE_ID,
    )
    assert (root, RDFS.label, Literal("Explicit", datatype=XSD.string)) in result
    assert set(graph) == before


@pytest.mark.parametrize("nested", [False, True])
def test_item_editor_rejects_datatype_loss_in_replaced_group(nested: bool) -> None:
    dto = resource("item")
    graph = resource_to_graph(dto)
    root = URIRef(dto.uri)
    if nested:
        node = next(graph.objects(root, BF.note))
        graph.add((node, RDFS.label, Literal("Typed note", datatype=XSD.string)))
        patch = ItemUpdate(note=[{"label": [{"value": "Replacement"}]}])
        unrelated = ItemUpdate(custodial_history=[])
    else:
        graph.add((root, BF.custodialHistory, Literal("Typed history", datatype=XSD.string)))
        patch = ItemUpdate(custodial_history=[{"value": "Replacement"}])
        unrelated = ItemUpdate(note=[])
    before = set(graph)
    with pytest.raises(RdfDocumentError, match="projeção DTO"):
        apply_update(graph, patch, identity(dto), profile=INSTITUTIONAL_PROFILE_ID)
    assert set(graph) == before
    preserved = apply_update(graph, unrelated, identity(dto), profile=INSTITUTIONAL_PROFILE_ID)
    assert any(isinstance(value, Literal) and value.datatype == XSD.string
               for value in preserved.objects())


def test_mutated_update_is_revalidated() -> None:
    dto = resource()
    graph = resource_to_graph(dto)
    before = set(graph)
    patch = WorkUpdate()
    patch.title = []
    with pytest.raises(ValidationError):
        apply_update(graph, patch, identity(dto), profile=INSTITUTIONAL_PROFILE_ID)
    assert set(graph) == before


def test_legacy_persistence_preparation_keeps_complete_patch() -> None:
    import json

    from libris.modules.bibliographic.application.service import prepare_document

    dto = resource()
    graph = resource_to_graph(dto)
    graph.add(
        (URIRef(dto.uri), DCTERMS.abstract, Literal("Resumo histórico", lang="pt"))
    )
    patched = apply_update(
        graph, WorkUpdate(note=None), identity(dto), profile=INSTITUTIONAL_PROFILE_ID
    )
    document = prepare_document(patched, dto.uri, INSTITUTIONAL_PROFILE_ID)
    assert isomorphic(patched, parse_document(json.dumps(document), "json-ld"))


@pytest.mark.parametrize(
    "name,predicate", [("work", BF.contribution), ("instance", BF.provisionActivity)]
)
def test_graph_read_rejects_auxiliary_self_references(
    name: str, predicate: URIRef
) -> None:
    dto = resource(name)
    graph = resource_to_graph(dto)
    auxiliary = next(graph.objects(URIRef(dto.uri), predicate))
    graph.remove((auxiliary, BF.agent, None))
    graph.add((auxiliary, BF.agent, URIRef(dto.uri)))
    with pytest.raises(RdfDocumentError):
        graph_to_read(graph, identity(dto))


def test_release_only_authorizes_its_editable_fields() -> None:
    dto = resource()
    profile = load_profile_registry().resolve("urn:libris:profile:work-minimal:2.0.0")
    minimal = WorkCreate(title=[{"main_title": [{"value": "Minimal"}]}])
    graph = create_to_graph(minimal, identity(dto), profile=profile)
    before = set(graph)
    with pytest.raises(CatalogError) as error:
        apply_update(
            graph,
            WorkUpdate(language=[{"uri": "urn:language"}]),
            identity(dto),
            profile=profile,
        )
    assert error.value.code.value == "profile.policy"
    assert set(graph) == before
