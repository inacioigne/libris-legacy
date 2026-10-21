"""Conformidade independente de PostgreSQL/Elasticsearch da representação v1."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from libris.modules.bibliographic.api.schemas import (
    AgentRead,
    InstanceRead,
    ItemRead,
    SubjectRead,
    WorkRead,
)
from libris.modules.bibliographic.application.relationships import ResolvedEntity
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.infrastructure.ownership import (
    check_snapshot,
    compose_snapshots,
)
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    RdfDocumentError,
    RdfFormat,
    new_graph,
    parse_document,
)
from libris.modules.bibliographic.infrastructure.serialization import (
    ReferenceMapping,
    RepresentationFormat,
    ResourceRead,
    context_digest,
    local_context,
    negotiate_format,
    parse_representation,
    resource_to_graph,
    serialize_composed,
    serialize_graph,
    serialize_resource,
)
from libris.modules.bibliographic.infrastructure.serialization.negotiation import (
    NotAcceptable,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    validate_profile,
)
from pydantic import ValidationError
from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, OWL, PROV, RDF, RDFS, SKOS, XSD

FIXTURES = Path(__file__).parents[1] / "fixtures/bibliographic/serialization"
EXAMPLES = Path(__file__).parents[2] / "docs/examples/serialization"
SCHEMAS = {
    "work": WorkRead,
    "minimal-work": WorkRead,
    "instance": InstanceRead,
    "item": ItemRead,
    "agent": AgentRead,
    "organization": AgentRead,
    "subject": SubjectRead,
}
FORMATS: tuple[RdfFormat, ...] = ("json-ld", "turtle")
JSON, JSON_LD, TURTLE = RepresentationFormat


def load(name: str) -> ResourceRead:
    return SCHEMAS[name].model_validate_json((FIXTURES / "valid" / f"{name}.json").read_text())


def subject_resolution() -> ReferenceMapping:
    subject = load("subject")
    entity = ResolvedEntity(ResourceIdentity(subject.internal_id, subject.uri), EntityType.SUBJECT)
    return {subject.internal_id: entity, subject.uri: entity}


@pytest.mark.parametrize("name", SCHEMAS)
def test_five_resources_owned_equivalence_and_no_mutation(name: str) -> None:
    resource = load(name)
    before = resource.model_dump(mode="json")
    graph = resource_to_graph(resource)
    assert check_snapshot(graph, URIRef(resource.uri)) is not None
    assert not list(graph.triples((None, OWL.sameAs, None)))
    assert not list(graph.triples((None, BF.hasInstance, None)))
    assert not list(graph.triples((None, BF.hasItem, None)))
    assert Literal(str(resource.internal_id)) not in graph.objects()
    profile = (
        AUTHORITY_PROFILE_ID
        if isinstance(resource, (AgentRead, SubjectRead))
        else INSTITUTIONAL_PROFILE_ID
    )
    assert validate_profile(graph, profile).conforms
    recovered = []
    for format in FORMATS:
        document = serialize_resource(resource, format=format)
        parsed = parse_representation(document, format)
        assert isomorphic(graph, parsed)
        assert isomorphic(graph, parse_document(document, format))
        assert len(parsed) == len(graph)
        recovered.append(parsed)
    assert isomorphic(*recovered)
    assert resource.model_dump(mode="json") == before


def test_work_structure_and_all_fields() -> None:
    resource = load("work")
    assert isinstance(resource, WorkRead)
    graph = resource_to_graph(resource)
    root = URIRef(resource.uri)
    assert (root, RDF.type, BF.Work) in graph
    titles = list(graph.objects(root, BF.title))
    assert len(titles) == 2
    assert {graph.value(node, RDF.type) for node in titles} == {
        BF.Title,
        BF.VariantTitle,
    }
    assert Literal("Synthetic monograph — 日本語", lang="en") in graph.objects()
    assert Literal("Título sem idioma") in graph.objects()
    assert Literal("Ensaios em São Luís", lang="pt-BR") in graph.objects()
    for field, predicate in [
        ("language", BF.language),
        ("content", BF.content),
        ("subject", BF.subject),
        ("related_to", BF.relatedTo),
        ("translation_of", BF.translationOf),
    ]:
        assert list(graph.objects(root, predicate)) == [URIRef(getattr(resource, field)[0].uri)]
    contributions = list(graph.objects(root, BF.contribution))
    assert len(contributions) == 2
    for node in contributions:
        assert (node, RDF.type, BF.Contribution) in graph
        assert len(list(graph.objects(node, BF.agent))) == 1
    assert len(list(graph.triples((None, BF.role, None)))) == 3
    classification = graph.value(root, BF.classification)
    assert (classification, RDF.type, BF.Classification) in graph
    assert (classification, BF.classificationPortion, Literal("TEST-001")) in graph
    assert (
        classification,
        BF.source,
        URIRef("urn:libris:test:classification"),
    ) in graph
    note = graph.value(root, BF.note)
    assert (note, RDF.type, BF.Note) in graph
    assert (note, RDFS.label, Literal("Nota Unicode: ç, é, 日本語", lang="pt")) in graph
    assert set(graph.objects(note, BF.noteType)) == {
        Literal("bibliography"),
        Literal("synthetic"),
    }
    assert set(graph.subjects(RDF.type, BF.Work)) == {root}
    assert not list(graph.subjects(RDF.type, BF.Agent))


def test_instance_structure_identifiers_publication_extent() -> None:
    resource = load("instance")
    assert isinstance(resource, InstanceRead)
    graph = resource_to_graph(resource)
    root = URIRef(resource.uri)
    assert (root, BF.instanceOf, URIRef(resource.instance_of.uri)) in graph
    assert (root, BF.carrier, URIRef(resource.carrier[0].uri)) in graph
    assert (
        root,
        BF.editionStatement,
        Literal("Edição sintética 2", lang="pt"),
    ) in graph
    publication = graph.value(root, BF.provisionActivity)
    assert (publication, RDF.type, BF.Publication) in graph
    assert (
        publication,
        BF.agent,
        URIRef(resource.provision_activity[0].agent[0].uri),
    ) in graph
    assert (
        publication,
        BF.place,
        URIRef("https://places.example.invalid/test"),
    ) in graph
    dates = set(graph.objects(publication, BF.date))
    assert dates == {
        Literal("2026", datatype=XSD.gYear),
        Literal("2026-02-28", datatype=XSD.date),
        Literal("[2026?]", datatype=XSD.string),
    }
    isbn = next(graph.subjects(RDF.type, BF.Isbn))
    assert (isbn, RDF.value, Literal("9780000000002")) in graph
    assert (isbn, BF.source, URIRef("urn:libris:test:isbn-source")) in graph
    assert list(graph.subjects(RDF.type, BF.Identifier))
    extent = graph.value(root, BF.extent)
    assert (extent, RDF.type, BF.Extent) in graph
    assert (extent, RDFS.label, Literal("120 páginas", lang="pt")) in graph


def test_item_structure_and_shelfmark_contract() -> None:
    resource = load("item")
    assert isinstance(resource, ItemRead)
    graph = resource_to_graph(resource)
    root = URIRef(resource.uri)
    assert (root, BF.itemOf, URIRef(resource.item_of.uri)) in graph
    assert (root, BF.heldBy, URIRef(resource.held_by[0].uri)) in graph
    identifier = next(graph.subjects(RDF.type, BF.Local))
    assert (identifier, RDF.value, Literal("TEST-3")) in graph
    assert (identifier, BF.source, URIRef("urn:libris:test:institution")) in graph
    shelfmark = graph.value(root, BF.shelfMark)
    assert (shelfmark, RDF.type, BF.ShelfMark) in graph
    assert (shelfmark, RDF.value, Literal("TEST 001")) in graph
    assert (shelfmark, BF.source, URIRef("urn:libris:test:shelving")) in graph
    assert not list(graph.triples((None, BF.itemLocation, None)))
    assert (root, BF.custodialHistory, Literal("Doação sintética", lang="pt")) in graph
    assert (root, PROV.wasDerivedFrom, URIRef(resource.provenance[0].uri)) in graph


@pytest.mark.parametrize("kind", ["Agent", "Person", "Organization"])
def test_agent_types_labels_and_external_links(kind: str) -> None:
    resource = AgentRead.model_validate(load("agent").model_dump() | {"kind": kind})
    graph = resource_to_graph(resource)
    root = URIRef(resource.uri)
    assert (root, RDF.type, BF[kind]) in graph
    assert (root, RDFS.label, Literal("Autoridade fictícia 10", lang="pt")) in graph
    assert (root, RDFS.label, Literal("Synthetic person", lang="en")) in graph
    assert (root, SKOS.altLabel, Literal("Variante fictícia 10", lang="pt")) in graph
    assert (root, RDFS.seeAlso, URIRef(resource.external_reference[0].uri)) in graph
    assert (root, DCTERMS.relation, URIRef(resource.related_authority[0].uri)) in graph
    assert (root, PROV.wasDerivedFrom, URIRef(resource.provenance[0].uri)) in graph
    assert list(graph.subjects(RDF.type, BF.Identifier))


def test_subject_skos_hierarchy() -> None:
    resource = load("subject")
    assert isinstance(resource, SubjectRead)
    graph = resource_to_graph(resource)
    root = URIRef(resource.uri)
    assert (root, RDF.type, SKOS.Concept) in graph
    assert (root, SKOS.prefLabel, Literal("Assunto sintético", lang="pt")) in graph
    assert (root, SKOS.prefLabel, Literal("Synthetic subject", lang="en")) in graph
    assert (root, SKOS.altLabel, Literal("Variante de assunto", lang="pt")) in graph
    for predicate, values in [
        (SKOS.inScheme, resource.scheme),
        (SKOS.broader, resource.broader),
        (SKOS.related, resource.related),
        (RDFS.seeAlso, resource.external_reference),
        (PROV.wasDerivedFrom, resource.provenance),
    ]:
        assert set(graph.objects(root, predicate)) == {URIRef(value.uri) for value in values}


def test_absence_and_rdf_set_semantics() -> None:
    resource = load("minimal-work")
    graph = resource_to_graph(resource)
    assert len(graph) == 4
    assert not list(graph.objects(URIRef(resource.uri), BF.language))
    raw = resource.model_dump()
    raw["language"] = [{"uri": "urn:test:language"}] * 2
    graph = resource_to_graph(WorkRead.model_validate(raw))
    assert len(list(graph.objects(URIRef(resource.uri), BF.language))) == 1
    assert Literal("Obra isolada", lang="pt-BR") not in graph.objects()


@pytest.mark.parametrize("format", FORMATS)
def test_direct_canonical_graph_preserves_extension_and_namespaces(
    format: RdfFormat,
) -> None:
    resource = load("minimal-work")
    graph = resource_to_graph(resource)
    root = URIRef(resource.uri)
    private = URIRef(resource.uri + "#private")
    graph.add((root, URIRef("https://extension.example.invalid/structure"), private))
    graph.add(
        (
            private,
            URIRef("https://extension.example.invalid/data"),
            Literal("Opaque value", datatype=URIRef("urn:libris:test:datatype")),
        )
    )
    before = set(graph)
    namespaces = tuple(graph.namespaces())
    document = serialize_graph(graph, format=format, root=resource.uri)
    assert isomorphic(graph, parse_representation(document, format))
    assert set(graph) == before
    assert tuple(graph.namespaces()) == namespaces
    assert len(resource_to_graph(resource)) < len(graph)


@pytest.mark.parametrize("format", FORMATS)
def test_explicit_composition_blank_nodes_and_inverses(format: RdfFormat) -> None:
    resources = [
        load(name) for name in ["work", "instance", "item", "agent", "organization", "subject"]
    ]
    snapshots = [(URIRef(resource.uri), resource_to_graph(resource)) for resource in resources]
    before = [set(graph) for _, graph in snapshots]
    expected = compose_snapshots(snapshots)
    result = parse_representation(serialize_composed(snapshots, format=format), format)
    assert isomorphic(expected, result)
    assert (
        URIRef(resources[0].uri),
        BF.hasInstance,
        URIRef(resources[1].uri),
    ) in result
    assert (URIRef(resources[1].uri), BF.hasItem, URIRef(resources[2].uri)) in result
    assert before == [set(graph) for _, graph in snapshots]
    with pytest.raises(ValueError):
        serialize_composed([snapshots[0], snapshots[0]], format=format)
    # Identical blank labels from independently parsed snapshots must remain separate.
    left, right = new_graph(), new_graph()
    for graph, root, label in [
        (left, URIRef("https://example.invalid/a"), "A"),
        (right, URIRef("https://example.invalid/b"), "B"),
    ]:
        graph.add((root, RDF.type, BF.Work))
        graph.add((root, BF.title, BNode("shared")))
        graph.add((BNode("shared"), RDF.type, BF.Title))
        graph.add((BNode("shared"), BF.mainTitle, Literal(label)))
    result = parse_representation(
        serialize_composed(
            [
                (URIRef("https://example.invalid/a"), left),
                (URIRef("https://example.invalid/b"), right),
            ],
            format=format,
        ),
        format,
    )
    assert len(set(result.subjects(RDF.type, BF.Title))) == 2


def test_local_references_resolve_exact_stored_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESOURCE_BASE_URI", "https://changed.example.invalid/resources")
    resource = load("minimal-work")
    subject = load("subject")
    for fields in [
        {"internal_id": str(subject.internal_id)},
        {"uri": subject.uri},
        {"internal_id": str(subject.internal_id), "uri": subject.uri},
    ]:
        raw = resource.model_dump() | {
            "subject": [{"kind": "local", "entity_type": "Subject", **fields}]
        }
        local = WorkRead.model_validate(raw)
        graph = resource_to_graph(local, references=subject_resolution())
        assert (URIRef(resource.uri), BF.subject, URIRef(subject.uri)) in graph
    uri_only = WorkRead.model_validate(
        resource.model_dump()
        | {"subject": [{"kind": "local", "entity_type": "Subject", "uri": subject.uri}]}
    )
    assert (URIRef(resource.uri), BF.subject, URIRef(subject.uri)) in resource_to_graph(uri_only)
    dual = WorkRead.model_validate(raw)
    with pytest.raises(RdfDocumentError):
        resource_to_graph(dual)  # UUID requires caller-provided resolution, including dual ID.
    assert (URIRef(resource.uri), RDF.type, BF.Work) in graph


@pytest.mark.parametrize("alteration", ["uuid", "uri", "type", "conflict", "missing"])
def test_inconsistent_reference_mapping(alteration: str) -> None:
    resource = load("minimal-work")
    subject = load("subject")
    reference = {
        "kind": "local",
        "entity_type": "Subject",
        "internal_id": str(subject.internal_id),
        "uri": subject.uri,
    }
    dto = WorkRead.model_validate(resource.model_dump() | {"subject": [reference]})
    mapping = dict(subject_resolution())
    resolved = mapping[subject.internal_id]
    if alteration == "uuid":
        mapping[subject.internal_id] = ResolvedEntity(
            ResourceIdentity(UUID(int=99), subject.uri), EntityType.SUBJECT
        )
    elif alteration == "uri":
        mapping[subject.internal_id] = ResolvedEntity(
            ResourceIdentity(subject.internal_id, "https://example.invalid/other"),
            EntityType.SUBJECT,
        )
    elif alteration == "type":
        mapping[subject.internal_id] = ResolvedEntity(resolved.identity, EntityType.AGENT)
    elif alteration == "conflict":
        mapping[subject.uri] = ResolvedEntity(
            ResourceIdentity(UUID(int=99), "https://example.invalid/other"),
            EntityType.SUBJECT,
        )
    else:
        mapping.clear()
    with pytest.raises(RdfDocumentError):
        resource_to_graph(dto, references=mapping)


@pytest.mark.parametrize(
    "entry",
    json.loads((FIXTURES / "manifest.json").read_text()),
    ids=lambda entry: entry["file"],
)
def test_invalid_fixtures(entry: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Tentativa de acesso externo")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("rdflib.parser.URLInputSource", forbidden)
    document = (FIXTURES / entry["file"]).read_text()
    if entry["layer"] == "parser":
        format: RdfFormat = "json-ld" if entry["format"] == "json-ld" else "turtle"
        with pytest.raises(RdfDocumentError):
            parse_representation(document, format)
    elif entry["layer"] == "pydantic":
        with pytest.raises(ValidationError):
            SCHEMAS[entry["schema"]].model_validate_json(document)
    else:
        dto = SCHEMAS[entry["schema"]].model_validate_json(document)
        with pytest.raises(RdfDocumentError):
            resource_to_graph(
                dto,
                references=None
                if entry["file"].endswith("/unresolved.json")
                else subject_resolution(),
            )


def test_revalidation_rejects_mutated_dto_and_self_reference() -> None:
    dto = load("minimal-work")
    dto.uri = "bad uri"
    with pytest.raises(ValidationError):
        resource_to_graph(dto)
    original = load("minimal-work")
    raw = original.model_dump() | {"translation_of": [{"uri": original.uri}]}
    with pytest.raises(RdfDocumentError):
        resource_to_graph(WorkRead.model_validate(raw))


@pytest.mark.parametrize("format", FORMATS)
def test_invalid_graphs_and_output_limits(
    format: RdfFormat, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RdfDocumentError):
        serialize_graph(Dataset(), format=format)
    root = URIRef("https://example.invalid/work")
    for triple in [
        (root, URIRef("relative"), Literal("a")),
        (root, RDF.type, Literal("Work")),
        (root, URIRef("urn:p"), Literal("bad", datatype=XSD.integer)),
        (root, URIRef("urn:p"), Literal("a", datatype=URIRef("relative"))),
        (URIRef("https://bad host/work"), RDF.type, BF.Work),
    ]:
        graph = new_graph()
        graph.add(triple)
        with pytest.raises(RdfDocumentError):
            serialize_graph(graph, format=format)
    graph = resource_to_graph(load("minimal-work"))
    with pytest.raises(ValueError):
        serialize_graph(graph, format=format, root="https://example.invalid/wrong")
    monkeypatch.setenv("RDF_MAX_DOCUMENT_BYTES", "8")
    with pytest.raises(RdfDocumentError):
        serialize_graph(graph, format=format)


def test_document_bytes_depth_and_keyword_safety() -> None:
    with pytest.raises(RdfDocumentError):
        parse_representation("á" * 10, "turtle", limit=10)
    value: object = {"@id": "urn:s"}
    for _ in range(70):
        value = [value]
    with pytest.raises(RdfDocumentError):
        parse_representation(json.dumps(value), "json-ld")
    graph = new_graph()
    parent = URIRef("urn:s")
    for index in range(70):
        child = BNode(str(index))
        graph.add((parent, URIRef("urn:child"), child))
        parent = child
    with pytest.raises(RdfDocumentError):
        serialize_graph(graph, format="json-ld")
    graph = new_graph()
    for keyword in ["@context", "@graph", "@import"]:
        graph.add((URIRef("urn:s"), RDFS.label, Literal(keyword)))
    assert isomorphic(
        graph, parse_representation(serialize_graph(graph, format="json-ld"), "json-ld")
    )


@pytest.mark.parametrize(
    "document",
    [
        {"@id": "relative"},
        {"@id": "urn:s", "@type": 1},
        {"@id": ["urn:s"]},
        {"@id": "urn:s", "title": {"@id": "urn:t"}},
        {"@context": [local_context()]},
        {"@context": {"g": "@graph"}},
        {"@context": local_context() | {"@vocab": "urn:unknown:"}},
        {"@context": local_context(), "@reverse": {}},
    ],
)
def test_untrusted_jsonld_policy(document: object) -> None:
    with pytest.raises(RdfDocumentError):
        parse_representation(json.dumps(document), "json-ld")


def test_context_version_integrity_and_freshness() -> None:
    context = local_context()
    assert context["@version"] == 1.1
    assert "@vocab" not in context and "@import" not in context
    assert context["agent"] == {"@id": "bf:agent", "@type": "@id"}
    assert context_digest() == "bfefbcf7a9db2811487d174879dcf311c066a935807cf661b805dd19f7482f16"
    context["agent"] = "broken"
    assert local_context()["agent"] != "broken"


@pytest.mark.parametrize(
    "name", ["work", "instance", "item", "agent", "organization", "subject", "related"]
)
def test_paired_examples_are_executable(name: str) -> None:
    document = (EXAMPLES / f"{name}.json").read_text()
    if name == "related":
        raws = json.loads(document)
        resources = [
            SCHEMAS[key].model_validate(raw)
            for key, raw in zip(
                ["work", "instance", "item", "agent", "subject", "organization"],
                raws,
                strict=True,
            )
        ]
        expected = compose_snapshots(
            [(URIRef(resource.uri), resource_to_graph(resource)) for resource in resources]
        )
    else:
        expected = resource_to_graph(SCHEMAS[name].model_validate_json(document))
    for format, extension in [("json-ld", "jsonld"), ("turtle", "ttl")]:
        actual = parse_representation((EXAMPLES / f"{name}.{extension}").read_text(), format)
        assert isomorphic(expected, actual)


@pytest.mark.parametrize(
    "accept,expected",
    [
        ("application/json", JSON),
        ("application/ld+json", JSON_LD),
        ("text/turtle", TURTLE),
        ("application/ld+json, text/turtle;q=0.5", JSON_LD),
        ("application/json;q=0, text/turtle;q=1", TURTLE),
        ("*/*", JSON),
        ("application/*", JSON),
        (None, JSON),
        ("application/ld+json;q=0.8, text/turtle;q=0.8", JSON_LD),
        ("text/turtle;q=0.8, application/ld+json;q=0.8", JSON_LD),
        ("application/json;q=0, */*;q=1", JSON_LD),
        ("application/*;q=0, */*;q=0.5", TURTLE),
        ("text/turtle;q=0, text/*;q=1, application/json;q=0.1", JSON),
        ("application/*;q=0.9, text/turtle;q=0.9", TURTLE),
        ("APPLICATION/LD+JSON;Q=1.000", JSON_LD),
        ('application/ld+json;profile="urn:unknown", text/turtle', TURTLE),
        ('application/ld+json;profile="a,b;c", text/turtle', TURTLE),
        ("application/json;q=0, application/json;q=1, text/turtle", TURTLE),
        (",,application/json,", JSON),
        ("application/xml, text/turtle;q=0.1", TURTLE),
    ],
)
def test_negotiation(accept: str | None, expected: RepresentationFormat) -> None:
    assert negotiate_format(accept) == expected


@pytest.mark.parametrize(
    "accept",
    [
        "application/xml",
        "",
        " , ",
        "*/*;q=0",
        "application/*;q=0, text/*;q=0",
        "application/json;q=1.1",
        "text/turtle;q=-1",
        "application/json;q=abc",
        "application/json;q=0.1234",
        "application/json;q=.5",
        'application/json;q="1"',
        "application/json;q=1;q=0",
        "*/json",
        "application",
        "application/json;invalid",
        "application/json;",
        'application/json;profile="unclosed',
        "application/json\r\ntext/turtle",
        'application/json;profile="urn:unknown"',
    ],
)
def test_negotiation_not_acceptable(accept: str) -> None:
    with pytest.raises(NotAcceptable):
        negotiate_format(accept)


def test_negotiation_available_formats_and_configured_default() -> None:
    available = (JSON_LD, TURTLE)
    assert negotiate_format(None, available=available, default=JSON_LD) == JSON_LD
    assert negotiate_format("*/*", available=available, default=TURTLE) == TURTLE
    with pytest.raises(NotAcceptable):
        negotiate_format("application/json", available=available, default=JSON_LD)
    with pytest.raises(ValueError):
        negotiate_format(None, available=available)


@pytest.mark.parametrize("format", FORMATS)
def test_canonical_rdf_collection_and_cycle(format: RdfFormat) -> None:
    graph = parse_document(
        '<urn:root> <urn:ordered> ("first" "second") ; <urn:cycle> _:a . '
        "_:a <urn:next> _:b . _:b <urn:next> _:a .",
        "turtle",
    )
    assert isomorphic(graph, parse_representation(serialize_graph(graph, format=format), format))


def test_collection_order_is_not_semantic() -> None:
    original = load("work")
    raw = original.model_dump()
    for field in ["title", "contribution"]:
        raw[field].reverse()
    assert isomorphic(resource_to_graph(original), resource_to_graph(WorkRead.model_validate(raw)))


@pytest.mark.parametrize("format", FORMATS)
def test_unrepresentable_lexical_form_is_explicit_error(format: RdfFormat) -> None:
    graph = new_graph()
    graph.add(
        (
            URIRef("urn:root"),
            URIRef("urn:number"),
            Literal("001", datatype=XSD.integer, normalize=False),
        )
    )
    with pytest.raises(RdfDocumentError, match="semântica"):
        serialize_graph(graph, format=format)


@pytest.mark.parametrize("format", FORMATS)
def test_unicode_iris_preserved(format: RdfFormat) -> None:
    resource = WorkRead.model_validate(
        load("minimal-work").model_dump()
        | {
            "uri": "https://catalog.example.invalid/obras/日本語",
            "subject": [{"uri": "https://subjects.example.invalid/ciência"}],
        }
    )
    recovered = parse_representation(serialize_resource(resource, format=format), format)
    assert (
        URIRef(resource.uri),
        BF.subject,
        URIRef("https://subjects.example.invalid/ciência"),
    ) in recovered


def test_orphan_blank_cycle_refuses_lossy_jsonld() -> None:
    graph = parse_document("_:a <urn:next> _:b . _:b <urn:next> _:a .", "turtle")
    with pytest.raises(RdfDocumentError, match="semântica"):
        serialize_graph(graph, format="json-ld")


@pytest.mark.parametrize(
    "document",
    [
        "123",
        "[null]",
        "[1]",
        '{"@id":"urn:a", "@id":"urn:b"}',
        '{"@id":"urn:a", "urn:p":{"@value":null}}',
        '{"@id":"urn:a", "urn:p":{"@value":{"data":1}}}',
    ],
)
def test_invalid_jsonld_structure_is_not_silently_dropped(document: str) -> None:
    with pytest.raises(RdfDocumentError):
        parse_representation(document, "json-ld")


def test_publication_self_reference_rejected() -> None:
    original = load("instance")
    payload = original.model_dump() | {"provision_activity": [{"agent": [{"uri": original.uri}]}]}
    with pytest.raises(RdfDocumentError):
        resource_to_graph(InstanceRead.model_validate(payload))
