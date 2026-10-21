"""Issue #30: cross-layer evidence using the existing synthetic contract corpus."""

import json
import subprocess
import sys
from pathlib import Path

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
from libris.modules.bibliographic.application.service import prepare_document
from libris.modules.bibliographic.domain.profile_contract import (
    ProfileValidationContext,
)
from libris.modules.bibliographic.infrastructure.profiles import load_profile_registry
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    RdfFormat,
    parse_document,
)
from libris.modules.bibliographic.infrastructure.serialization import (
    parse_representation,
    resource_to_graph,
    serialize_graph,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    PROFILE_ID,
    validate_profile,
    validate_resolved_profile,
)
from rdflib import URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import RDF, RDFS, SH, SKOS

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/bibliographic"
CONTRACTS = {
    "work": (WorkCreate, WorkRead, WorkUpdate, BF.Work, BF.title),
    "instance": (InstanceCreate, InstanceRead, InstanceUpdate, BF.Instance, BF.instanceOf),
    "item": (ItemCreate, ItemRead, ItemUpdate, BF.Item, BF.itemOf),
    "agent": (AgentCreate, AgentRead, AgentUpdate, BF.Person, RDFS.label),
    "subject": (SubjectCreate, SubjectRead, SubjectUpdate, SKOS.Concept, SKOS.prefLabel),
}


@pytest.mark.parametrize("name", CONTRACTS)
@pytest.mark.parametrize("operation", ["create", "read", "update"])
def test_contract_pipeline_preserves_identity_and_semantics(name: str, operation: str) -> None:
    create_type, read_type, update_type, rdf_class, _ = CONTRACTS[name]
    original = read_type.model_validate_json(
        (FIXTURES / "contracts" / f"{name}-read.json").read_text()
    )
    payload = original.model_dump(mode="json")
    if operation == "create":
        created = create_type.model_validate_json(
            (FIXTURES / "contracts" / f"{name}-create.json").read_text()
        )
        payload = {
            **created.model_dump(mode="json"),
            "internal_id": str(original.internal_id),
            "uri": original.uri,
        }
    elif operation == "update":
        patch = update_type.model_validate_json(
            (FIXTURES / "contracts" / f"{name}-update.json").read_text()
        )
        # Test composition only: there is deliberately no public DTO PATCH service.
        for field, value in patch.model_dump(mode="json", exclude_unset=True).items():
            payload[field] = [] if value is None else value
    resource = read_type.model_validate(payload)
    assert (resource.internal_id, resource.uri) == (original.internal_id, original.uri)
    graph = resource_to_graph(resource)
    assert (URIRef(original.uri), RDF.type, rdf_class) in graph
    if operation == "create":
        assert isomorphic(graph, resource_to_graph(original))
    profile_id = AUTHORITY_PROFILE_ID if name in {"agent", "subject"} else INSTITUTIONAL_PROFILE_ID
    snapshot = prepare_document(graph, resource.uri, profile_id)
    assert isomorphic(graph, parse_document(json.dumps(snapshot), "json-ld"))
    profile = load_profile_registry().resolve(f"urn:libris:profile:{name}:2.0.0")
    context = ProfileValidationContext(profile_release=profile.release_id)
    assert validate_resolved_profile(graph, profile, context).conforms
    # Both formats must preserve all terms, including BNodes, datatypes and languages.
    for format in ("json-ld", "turtle"):
        restored = parse_representation(serialize_graph(graph, format=format), format)
        assert isomorphic(graph, restored)
        assert isomorphic(
            graph, parse_representation(serialize_graph(restored, format="turtle"), "turtle")
        )
    if operation == "update":
        original_graph = resource_to_graph(original)
        assert not isomorphic(graph, original_graph)
        removed_predicate = {
            "work": BF.subject,
            "instance": BF.editionStatement,
            "item": BF.heldBy,
            "agent": SKOS.altLabel,
            "subject": SKOS.altLabel,
        }[name]
        original_graph.remove((URIRef(original.uri), removed_predicate, None))
        assert isomorphic(graph, original_graph)
        assert original == read_type.model_validate_json(
            (FIXTURES / "contracts" / f"{name}-read.json").read_text()
        )


@pytest.mark.parametrize("name", CONTRACTS)
def test_required_rdf_properties_have_release_specific_diagnostics(name: str) -> None:
    _, read_type, _, _, predicate = CONTRACTS[name]
    resource = read_type.model_validate_json(
        (FIXTURES / "contracts" / f"{name}-read.json").read_text()
    )
    graph = resource_to_graph(resource)
    root = URIRef(resource.uri)
    graph.remove((root, predicate, None))
    before = set(graph)
    profile = load_profile_registry().resolve(f"urn:libris:profile:{name}:2.0.0")
    report = validate_resolved_profile(
        graph, profile, ProfileValidationContext(profile_release=profile.release_id)
    )
    assert not report.conforms
    assert any(
        issue.focus_term == root
        and issue.path_term == predicate
        and issue.severity == str(SH.Violation)
        and issue.source_constraint_component == str(SH.MinCountConstraintComponent)
        and issue.source_shape_term is not None
        for issue in report.issues
    )
    assert set(graph) == before


@pytest.mark.parametrize("format,suffix", [("json-ld", "jsonld"), ("turtle", "ttl")])
def test_legacy_book_examples_remain_equivalent(format: RdfFormat, suffix: str) -> None:
    original = parse_document((FIXTURES / "book.ttl").read_text(), "turtle")
    paired = parse_document((ROOT / "docs/examples" / f"book.{suffix}").read_text(), format)
    assert isomorphic(original, paired)
    assert validate_profile(paired, PROFILE_ID).conforms
    # Legacy aggregate lacks inScheme and uses embedded authorities: it must not
    # acquire institutional conformance just because a newer release is available.
    assert not validate_profile(paired, INSTITUTIONAL_PROFILE_ID).conforms


def test_public_diagnostic_examples_match_current_contracts() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_diagnostic_examples.py"), "--check"],
        check=True,
        capture_output=True,
        text=True,
    )
