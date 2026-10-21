import json
from pathlib import Path
from uuid import UUID

import pytest
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    RdfDocumentError,
    RdfFormat,
    add_resource,
    link_instance,
    link_item,
    new_graph,
    parse_document,
    serialize_document,
)
from libris.modules.bibliographic.infrastructure.validation import validate_monograph
from rdflib import BNode, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, RDF, SH, XSD

FIXTURE = Path(__file__).parents[1] / "fixtures/bibliographic/book.ttl"
WORK = URIRef("http://localhost:8000/resources/00000000-0000-4000-8000-000000000001")


def test_create_resource_relationships() -> None:
    graph = new_graph()
    work = add_resource(graph, "https://example.invalid/work", BF.Work)
    instance = add_resource(graph, "https://example.invalid/instance", BF.Instance)
    item = add_resource(graph, "https://example.invalid/item", BF.Item)
    link_instance(graph, instance, work)
    link_item(graph, item, instance)
    assert (work, RDF.type, BF.Work) in graph
    assert (instance, RDF.type, BF.Instance) in graph
    assert (item, RDF.type, BF.Item) in graph
    assert (instance, BF.instanceOf, work) in graph
    assert (work, BF.hasInstance, instance) in graph
    assert (item, BF.itemOf, instance) in graph
    assert (instance, BF.hasItem, item) in graph


@pytest.mark.parametrize("format", ["json-ld", "turtle"])
def test_round_trip(format: RdfFormat) -> None:
    graph = parse_document(FIXTURE.read_text(), "turtle")
    recovered = parse_document(serialize_document(graph, format), format)
    assert isomorphic(graph, recovered)
    assert any(isinstance(subject, BNode) for subject in recovered.subjects())
    assert Literal("Imaginary gardens", lang="en") in recovered.objects()
    assert Literal("Jardins imaginários", lang="pt") in recovered.objects()
    assert Literal("2026", datatype=XSD.gYear) in recovered.objects()
    assert len(list(recovered.objects(WORK, BF.subject))) == 2
    assert URIRef("https://example.invalid/works/related") in recovered.objects()


def test_profile_valid_and_invalid() -> None:
    graph = parse_document(FIXTURE.read_text(), "turtle")
    assert validate_monograph(graph).conforms
    graph.remove((WORK, BF.title, None))
    report = validate_monograph(graph)
    assert not report.conforms
    assert any(issue.severity == str(SH.Violation) for issue in report.issues)
    assert any("título" in message for issue in report.issues for message in issue.messages)


def test_profile_warning_and_inverse_consistency() -> None:
    graph = parse_document(FIXTURE.read_text(), "turtle")
    item = next(graph.subjects(RDF.type, BF.Item))
    graph.remove((item, BF.identifiedBy, None))
    report = validate_monograph(graph)
    assert report.conforms
    assert any(issue.severity == str(SH.Warning) for issue in report.issues)
    graph.remove((WORK, BF.hasInstance, None))
    assert not validate_monograph(graph).conforms


def test_stable_identity() -> None:
    identifier = UUID("00000000-0000-4000-8000-000000000001")
    identity = ResourceIdentity.create("http://localhost:8000/resources/", identifier)
    assert identity.uri == str(WORK)
    assert ResourceIdentity.create("http://localhost:8000/resources", identifier) == identity
    graph = parse_document(FIXTURE.read_text(), "turtle")
    graph.add((WORK, DCTERMS.description, Literal("Alteração")))
    assert (URIRef(identity.uri), RDF.type, BF.Work) in parse_document(
        serialize_document(graph, "json-ld"), "json-ld"
    )


@pytest.mark.parametrize(
    "context",
    [
        "https://example.invalid/context",
        {"@import": "file:///etc/passwd"},
        {"field": {"@id": "urn:field", "@context": "https://example.invalid/context"}},
        ["https://example.invalid/context"],
    ],
)
def test_reject_unsafe_contexts(context: object, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_access(*args: object, **kwargs: object) -> None:
        pytest.fail("Parsing attempted an external request")

    monkeypatch.setattr("urllib.request.urlopen", unexpected_access)
    with pytest.raises(RdfDocumentError):
        parse_document(json.dumps({"@context": context}), "json-ld")


def test_local_context() -> None:
    graph = parse_document(
        json.dumps(
            {
                "@context": {"bf": str(BF), "title": "bf:mainTitle"},
                "@id": "https://example.invalid/title",
                "title": {"@value": "Título", "@language": "pt"},
            }
        ),
        "json-ld",
    )
    assert Literal("Título", lang="pt") in graph.objects()


@pytest.mark.parametrize(
    "document,format",
    [
        ("not turtle", "turtle"),
        ("{", "json-ld"),
        ('{"@id":"urn:test", "@graph":[]}', "json-ld"),
        ('<urn:s> <urn:p> "abc"^^<http://www.w3.org/2001/XMLSchema#integer> .', "turtle"),
    ],
)
def test_invalid_input(document: str, format: RdfFormat) -> None:
    with pytest.raises(RdfDocumentError):
        parse_document(document, format)


def test_size_limit() -> None:
    with pytest.raises(RdfDocumentError):
        parse_document("á" * 10, "turtle", limit=10)


def test_profile_is_declarative() -> None:
    from importlib.resources import files

    profile = json.loads(
        files("libris.modules.bibliographic").joinpath("resources/monograph-v1.json").read_text()
    )
    assert profile["version"] == 1
    assert {str(BF.Work), str(BF.Instance), str(BF.Item)} <= {
        resource["class"] for resource in profile["resources"]
    }
    assert any(
        field["repeatable"] for resource in profile["resources"] for field in resource["fields"]
    )
    assert (
        files("libris.modules.bibliographic")
        .joinpath("resources/" + profile["validationShapes"])
        .is_file()
    )


@pytest.mark.parametrize(
    "base",
    [
        "urn:test:",
        "https://example.invalid/?q=1",
        "https://user:password@example.invalid/resources",
    ],
)
def test_invalid_uri_base(base: str) -> None:
    with pytest.raises(ValueError):
        ResourceIdentity.create(base)


def test_limits_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RDF_MAX_DOCUMENT_BYTES", "8")
    with pytest.raises(RdfDocumentError):
        parse_document("<urn:s> <urn:p> <urn:o> .", "turtle")


@pytest.mark.parametrize(
    "document",
    [
        {"@context": {"ctx": "@context"}, "ctx": "https://example.invalid/context"},
        {"@context": {"g": "@graph"}, "g": []},
        {"@graph": [{"@graph": []}]},
    ],
)
def test_reject_keyword_aliases_and_nested_graphs(document: object) -> None:
    with pytest.raises(RdfDocumentError):
        parse_document(json.dumps(document), "json-ld")


def test_root_and_storage_constraints() -> None:
    from libris.modules.bibliographic.application.service import prepare_document

    graph = parse_document(FIXTURE.read_text(), "turtle")
    with pytest.raises(ValueError, match="URI raiz"):
        prepare_document(graph, "https://example.invalid/missing")
    graph.add((WORK, URIRef("urn:note"), Literal("\x00")))
    with pytest.raises(ValueError, match="NUL"):
        prepare_document(graph, str(WORK))


def test_refuse_conversion_that_changes_literal_terms() -> None:
    graph = new_graph()
    graph.add(
        (
            URIRef("urn:test"),
            URIRef("urn:value"),
            Literal("001", datatype=XSD.integer, normalize=False),
        )
    )
    with pytest.raises(RdfDocumentError, match="semântica"):
        serialize_document(graph, "json-ld")


def test_keywords_in_literals_are_data() -> None:
    graph = parse_document(
        json.dumps([{"@id": "urn:test", "urn:value": [{"@value": "@graph"}]}]), "json-ld"
    )
    assert Literal("@graph") in graph.objects()


def test_identity_requires_http_semantic_uri() -> None:
    with pytest.raises(ValueError, match='HTTP'):
        ResourceIdentity(UUID('00000000-0000-4000-8000-000000000001'), 'urn:invalid')
