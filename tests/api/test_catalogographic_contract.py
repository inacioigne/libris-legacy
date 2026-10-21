import json
from importlib.resources import files
from pathlib import Path
from uuid import UUID

import pytest
from libris.modules.bibliographic.application.service import prepare_document
from libris.modules.bibliographic.domain.models import (
    ResourceIdentity,
    StaleRevisionError,
    require_revision_token,
    revision_token,
)
from libris.modules.bibliographic.domain.profiles import CatalogProfile
from libris.modules.bibliographic.infrastructure.ownership import (
    ENTITY_TYPES,
    INVERSES,
    check_snapshot,
    compose_snapshots,
    owned_nodes,
)
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    RdfFormat,
    new_graph,
    parse_document,
    serialize_document,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    validate_profile,
)
from pydantic import ValidationError
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, RDF, RDFS, SH, SKOS

CORPUS = Path(__file__).parents[1] / "fixtures/bibliographic/institutional"
MANIFEST = json.loads((CORPUS / "manifest.json").read_text())


def fixture(name: str) -> Graph:
    return parse_document((CORPUS / f"{name}.ttl").read_text(), "turtle")


def snapshots(graph: Graph) -> list[tuple[URIRef, Graph]]:
    roots = {node for kind in ENTITY_TYPES for node in graph.subjects(RDF.type, kind)}
    result: list[tuple[URIRef, Graph]] = []
    for root in roots:
        assert isinstance(root, URIRef)
        nodes = owned_nodes(graph, root)
        snapshot = new_graph()
        for subject, predicate, value in graph:
            if subject in nodes:
                snapshot.add((subject, predicate, value))
        result.append((root, snapshot))
    return result


@pytest.mark.parametrize("case", MANIFEST["fixtures"], ids=lambda case: case["scenario"])
def test_corpus_shacl(case: dict[str, object]) -> None:
    graph = fixture(str(case["file"]).removesuffix(".ttl"))
    report = validate_profile(graph, INSTITUTIONAL_PROFILE_ID)
    assert report.conforms == case["valid"]
    if not case["valid"]:
        assert any(
            issue.severity == str(SH.Violation)
            and issue.path == case["expected_path"]
            and issue.source_constraint_component == case["expected_component"]
            and issue.source_shape_term is not None
            for issue in report.issues
        )
        assert any(True for _ in report.graph.subjects(RDF.type, SH.ValidationResult))


@pytest.mark.parametrize(
    "name", [case["scenario"] for case in MANIFEST["fixtures"] if case["valid"]]
)
@pytest.mark.parametrize("format", ["json-ld", "turtle"])
def test_corpus_lossless_entity_snapshots(name: str, format: RdfFormat) -> None:
    original = fixture(name)
    recovered: list[tuple[URIRef, Graph]] = []
    for root, graph in snapshots(original):
        kind = check_snapshot(graph, root)
        profile = (
            INSTITUTIONAL_PROFILE_ID
            if kind in (BF.Work, BF.Instance, BF.Item)
            else AUTHORITY_PROFILE_ID
        )
        document = prepare_document(graph, str(root), profile)
        assert isomorphic(graph, parse_document(json.dumps(document), "json-ld"))
        restored = parse_document(serialize_document(graph, format), format)
        assert isomorphic(graph, restored)
        recovered.append((root, restored))
    composed = compose_snapshots(recovered)
    assert validate_profile(composed, INSTITUTIONAL_PROFILE_ID, composed=True).conforms
    for inverse in INVERSES.values():
        composed.remove((None, inverse, None))
    assert isomorphic(original, composed)


def test_profile_schema_and_shacl_cardinalities() -> None:
    resources = files("libris.modules.bibliographic").joinpath("resources")
    schema = json.loads(resources.joinpath("catalog-profile.schema.json").read_text())
    assert schema == CatalogProfile.model_json_schema(by_alias=True)
    for name in ("monograph-v1.1", "authority-v1"):
        profile = CatalogProfile.model_validate_json(resources.joinpath(f"{name}.json").read_text())
        shapes = parse_document(resources.joinpath(profile.validation_shapes).read_text(), "turtle")
        for resource in profile.resources:
            owners = list(shapes.subjects(SH.targetClass, URIRef(resource.rdf_class)))
            assert owners
            for field in resource.fields:
                rules = [
                    rule
                    for owner in owners
                    for rule in shapes.objects(owner, SH.property)
                    if (rule, SH.path, URIRef(field.property)) in shapes
                    and shapes.value(rule, SH.nodeKind) is not None
                ]
                assert len(rules) == 1
                rule = rules[0]
                assert int(str(shapes.value(rule, SH.minCount))) == field.min_count
                maximum = shapes.value(rule, SH.maxCount)
                assert (int(str(maximum)) if maximum else None) == field.max_count
                assert shapes.value(rule, SH.nodeKind) == SH[field.node_kind]


def test_profile_rejects_inconsistent_form_contract() -> None:
    resources = files("libris.modules.bibliographic").joinpath("resources")
    data = json.loads(resources.joinpath("monograph-v1.1.json").read_text())
    data["resources"][0]["fields"][0]["requiredInForm"] = False
    with pytest.raises(ValidationError):
        CatalogProfile.model_validate(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("property", "relative-property"),
        ("targetClasses", ["RelativeClass"]),
        ("languages", True),
        ("repeatable", False),
        ("maxCount", 0),
        ("absent", "omit"),
    ],
)
def test_profile_rejects_invalid_resource_field(key: str, value: object) -> None:
    resources = files("libris.modules.bibliographic").joinpath("resources")
    data = json.loads(resources.joinpath("monograph-v1.1.json").read_text())
    data["resources"][0]["fields"][0][key] = value
    with pytest.raises(ValidationError):
        CatalogProfile.model_validate(data)


def test_shared_authority_is_a_reference_not_a_copy() -> None:
    graph = fixture("shared-authority")
    people = set(graph.subjects(RDF.type, BF.Person))
    assert len(people) == 1
    person = next(iter(people))
    assert len(set(graph.subjects(BF.agent, person))) == 2
    owned = snapshots(graph)
    for root, snapshot in owned:
        if root != person:
            assert not list(snapshot.predicate_objects(person))
    assert not list(graph.triples((None, OWL.sameAs, None)))


def test_multiple_editions_items_and_translation_ownership() -> None:
    editions = fixture("two-editions")
    assert len(set(editions.subjects(RDF.type, BF.Work))) == 1
    assert len(set(editions.subjects(RDF.type, BF.Instance))) == 2
    isbn_values = {editions.value(node, RDF.value) for node in editions.subjects(RDF.type, BF.Isbn)}
    assert len(isbn_values) == 2
    items = fixture("multiple-items")
    assert len(set(items.subjects(RDF.type, BF.Item))) == 2
    assert len(set(items.objects(None, BF.itemOf))) == 1
    translated = fixture("translated-monograph")
    translated_work, origin = next(translated.subject_objects(BF.translationOf))
    assert (translated_work, RDF.type, BF.Work) in translated
    assert (origin, RDF.type, BF.Work) in translated
    assert not list(translated.subjects(BF.editionStatement, translated_work))


def test_invalid_inverse_is_detected_without_rewriting_the_input() -> None:
    composed = compose_snapshots(snapshots(fixture("multiple-items")))
    work = next(composed.subjects(RDF.type, BF.Work))
    composed.remove((work, BF.hasInstance, None))
    assert not validate_profile(composed, INSTITUTIONAL_PROFILE_ID, composed=True).conforms
    assert not list(composed.objects(work, BF.hasInstance))


def test_snapshot_rejects_foreign_description_inverse_and_type_changes() -> None:
    graph = fixture("single-author")
    work = next(graph.subjects(RDF.type, BF.Work))
    assert isinstance(work, URIRef)
    with pytest.raises(ValueError, match="alheia"):
        check_snapshot(graph, work)
    root, snapshot = next((r, g) for r, g in snapshots(graph) if r == work)
    snapshot.add((root, BF.hasInstance, URIRef("urn:test:instance")))
    with pytest.raises(ValueError, match="inversas"):
        check_snapshot(snapshot, root)
    snapshot.remove((root, BF.hasInstance, None))
    snapshot.add((root, RDF.type, BF.Item))
    with pytest.raises(ValueError, match="exatamente um tipo"):
        check_snapshot(snapshot, root)


def test_composition_relabels_blank_nodes_and_rejects_duplicate_roots() -> None:
    first = URIRef("https://example.invalid/first")
    second = URIRef("https://example.invalid/second")
    parts: list[tuple[URIRef, Graph]] = []
    for root, text in ((first, "Primeira"), (second, "Segunda")):
        graph = new_graph()
        node = BNode("same-serialization-label")
        graph.add((root, RDF.type, BF.Work))
        graph.add((root, BF.title, node))
        graph.add((node, RDF.type, BF.Title))
        graph.add((node, BF.mainTitle, Literal(text, lang="pt")))
        parts.append((root, graph))
    composed = compose_snapshots(parts)
    assert len(set(composed.objects(None, BF.title))) == 2
    assert validate_profile(composed, INSTITUTIONAL_PROFILE_ID).conforms
    assert len(parts[0][1]) == 4
    with pytest.raises(ValueError, match="duas revisões"):
        compose_snapshots([parts[0], parts[0]])


def test_uri_and_write_token_contract() -> None:
    identities = [
        ResourceIdentity.create("https://catalog.example.invalid/resources/") for _ in range(5)
    ]
    assert len({identity.uri for identity in identities}) == 5
    for identity in identities:
        assert identity.uri.endswith(str(identity.internal_id))
        assert (
            ResourceIdentity.create(
                "https://catalog.example.invalid/resources", identity.internal_id
            )
            == identity
        )
        require_revision_token(identity.internal_id, 7, revision_token(identity.internal_id, 7))
        for invalid in ("*", "W/weak", revision_token(identity.internal_id, 6)):
            with pytest.raises(StaleRevisionError):
                require_revision_token(identity.internal_id, 7, invalid)
    with pytest.raises(StaleRevisionError):
        require_revision_token(
            identities[0].internal_id, 7, revision_token(identities[1].internal_id, 7)
        )
    with pytest.raises(ValueError):
        revision_token(UUID(int=1), 0)


def test_authority_and_concept_labels_have_distinct_rules() -> None:
    graph = fixture("controlled-subjects")
    concept = next(graph.subjects(RDF.type, SKOS.Concept))
    graph.add((concept, SKOS.altLabel, Literal("Assunto sintético", lang="pt")))
    assert not validate_profile(graph, AUTHORITY_PROFILE_ID).conforms
    graph = fixture("external-identifiers")
    person = next(graph.subjects(RDF.type, BF.Person))
    assert len(list(graph.objects(person, RDFS.seeAlso))) == 4


def test_absent_instance_title_and_isbn_are_valid() -> None:
    graph = fixture("without-isbn")
    assert not list(graph.subjects(RDF.type, BF.Isbn))
    instance = next(graph.subjects(RDF.type, BF.Instance))
    assert not list(graph.objects(instance, BF.title))
    assert validate_profile(graph, INSTITUTIONAL_PROFILE_ID).conforms
