"""Deterministic contract/membership/provider/RDF checks without external services."""

import asyncio
import json
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from rdflib import Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, RDF, SKOS, XSD

from libris.modules.bibliographic.api.schemas import WorkRead
from libris.modules.bibliographic.api.schemas.common import LocalizedText, ResourceReference
from libris.modules.bibliographic.api.schemas.controlled_values import CatalogValueDocument
from libris.modules.bibliographic.application.controlled_values import (
    ControlledConcept,
    EntityValue,
    LiteralValue,
    LocalConcept,
    parse_catalog_value,
)
from libris.modules.bibliographic.application.relationships import ResolvedEntity
from libris.modules.bibliographic.application.vocabulary_resolution import (
    EnrichedConcept,
    ResolutionCachePolicy,
    ResolutionFailure,
    ResolutionProvenance,
    ResolutionResult,
    ResolutionSuccess,
    resolve_concept,
)
from libris.modules.bibliographic.application.vocabulary_validation import (
    resolution_diagnostics,
    validate_catalog_value,
    validate_profile_vocabulary_membership,
)
from libris.modules.bibliographic.domain.errors import CatalogCode
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.profile_contract import (
    CatalogPolicy,
    DeclarativeField,
    IRIDefault,
    ProfileValidationContext,
    ReferenceValue,
)
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
    VocabularyConceptEvidence,
    VocabularyDefinition,
    VocabularyRegistry,
)
from libris.modules.bibliographic.infrastructure.profiles import (
    load_legacy_profile,
    load_profile_registry,
)
from libris.modules.bibliographic.infrastructure.rdf import BF, RdfDocumentError
from libris.modules.bibliographic.infrastructure.serialization import (
    parse_representation,
    resource_to_graph,
    serialize_graph,
)
from libris.modules.bibliographic.infrastructure.serialization.controlled_values import (
    catalog_value_to_graph,
    local_concept_to_graph,
)
from libris.modules.bibliographic.infrastructure.validation import validate_resolved_profile

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "docs/examples/controlled-vocabularies"
URI = "http://id.loc.gov/vocabulary/languages/por"
VOCABULARY = "http://id.loc.gov/vocabulary/languages/"
SOURCE = "urn:test:trusted-source"
NOW = datetime(2026, 10, 12, tzinfo=UTC)
ROOT_URI = "https://example.org/resources/00000000-0000-4000-8000-000000000001"


def concept(**updates: object) -> ControlledConcept:
    return ControlledConcept.model_validate(
        {"kind": "controlled_concept", "uri": URI, "vocabulary": VOCABULARY, **updates}
    )


def local() -> LocalConcept:
    value = CatalogValueDocument.model_validate_json((EXAMPLES / "local.json").read_text()).value
    assert isinstance(value, LocalConcept)
    return value


def field() -> DeclarativeField:
    profile = load_profile_registry().resolve("urn:libris:profile:core:2.0.0")
    return next(
        f
        for r in profile.resources
        if r.rdf_class == str(BF.Work)
        for f in r.fields
        if f.name == "language"
    )


def evidence(*, complete: bool, members: tuple[str, ...] = ()) -> VocabularyRegistry:
    return VocabularyRegistry(
        vocabularies=(
            VocabularyDefinition(
                uri=VOCABULARY,
                release="1.0",
                source=SOURCE,
                members=members,
                complete=complete,
            ),
        )
    )


def provenance() -> ResolutionProvenance:
    return ResolutionProvenance(provider="urn:test:provider", source=SOURCE, retrieved_at=NOW)


class StubProvider:
    def __init__(self, result: ResolutionResult) -> None:
        self.result = result
        self.uris: list[str] = []

    async def resolve(self, uri: str) -> ResolutionResult:
        self.uris.append(uri)
        return self.result


def test_multilingual_json_identity_metadata_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Implicit network access")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    document = CatalogValueDocument.model_validate_json((EXAMPLES / "external.json").read_text())
    assert isinstance(document.value, ControlledConcept)
    value = document.value
    assert value.status == "unverified" and value.uri == URI and value.notation == "por"
    assert [text.language for text in value.label] == ["pt", "en"]
    assert value.alternative_labels[0].value == "Língua portuguesa"
    assert CatalogValueDocument.model_validate_json(document.model_dump_json()) == document
    assert value.as_reference() == ResourceReference(uri=URI)
    assert concept().label == []  # Identity is sufficient for an unverified URI-only concept.
    assert len(catalog_value_to_graph(ROOT_URI, str(BF.language), value)) == 1


@pytest.mark.parametrize(
    "uri",
    [
        "relative",
        "https:///missing",
        "urn:",
        "http://x/%ZZ",
        "http://x/with space",
        "http://x:99999/",
    ],
)
def test_invalid_uri(uri: str) -> None:
    with pytest.raises(ValidationError):
        concept(uri=uri)


@pytest.mark.parametrize(
    "updates",
    [
        {"label": [{"value": "a", "language": "pt"}, {"value": "b", "language": "PT"}]},
        {"label": [{"value": "a"}, {"value": "b"}]},
        {"label": [{"value": " "}]},
        {"label": [{"value": "a", "language": "pt_BR"}]},
        {"alternative_labels": [{"value": "a"}, {"value": "a"}]},
        {
            "label": [{"value": "a", "language": "pt"}],
            "alternative_labels": [{"value": "a", "language": "PT"}],
        },
        {"status": "resolved"},
        {"status": "invalid"},
        {"status": "unknown"},
        {"replacement": "urn:test:replacement"},
        {"status": "deprecated", "source": SOURCE, "replacement": URI},
        {"language": "pt"},
        {"uri": None},
        {"vocabulary": None},
    ],
)
def test_inconsistent_concepts(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        concept(**updates)


def test_alternatives_allow_multiple_labels_per_language() -> None:
    value = concept(
        alternative_labels=[{"value": "a", "language": "pt"}, {"value": "b", "language": "pt"}]
    )
    assert len(value.alternative_labels) == 2


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"kind": "literal", "value": "Português", "language": "pt"}, LiteralValue),
        ({"kind": "controlled_concept", "uri": URI, "vocabulary": VOCABULARY}, ControlledConcept),
        (
            {
                "kind": "entity_reference",
                "reference": {
                    "kind": "external",
                    "entity_type": "Agent",
                    "uri": "https://viaf.org/viaf/123",
                },
            },
            EntityValue,
        ),
    ],
)
def test_discrimination(payload: dict[str, object], expected: type) -> None:
    assert isinstance(parse_catalog_value(payload), expected)
    assert isinstance(parse_catalog_value(local().model_dump()), LocalConcept)


@pytest.mark.parametrize(
    "payload",
    [
        {"uri": URI},
        {"kind": "controlled_concept", "uri": URI},
        {"kind": "literal", "value": "text", "uri": URI},
        {"kind": "literal", "value": 42},
        {"kind": "literal", "value": "x", "language": "pt", "datatype": str(XSD.string)},
        {"kind": "literal", "value": "abc", "datatype": str(XSD.integer)},
        {"kind": "entity_reference", "uri": URI},
        {"kind": "local_concept", "uri": URI, "vocabulary": VOCABULARY},
    ],
)
def test_ambiguous_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_catalog_value(payload)


def test_local_stored_identity_never_derived_from_label() -> None:
    value = local()
    payload = value.model_dump()
    payload["label"] = [{"value": "Novo nome", "language": "pt"}]
    renamed = LocalConcept.model_validate(payload)
    assert renamed.uri == value.uri and renamed.internal_id == value.internal_id
    payload["uri"] = value.uri + "?query"
    with pytest.raises(ValidationError):
        LocalConcept.model_validate(payload)


@pytest.mark.parametrize(
    "status,code,severity",
    [
        ("unverified", CatalogCode.REFERENCE_UNVERIFIED, "Warning"),
        ("deprecated", CatalogCode.REFERENCE_DEPRECATED, "Warning"),
        ("invalid", CatalogCode.REFERENCE_INVALID, "Violation"),
        ("unavailable", CatalogCode.RESOLVER_UNAVAILABLE, "Warning"),
    ],
)
def test_known_states_use_existing_diagnostics(
    status: str, code: CatalogCode, severity: str
) -> None:
    issues = validate_catalog_value(concept(status=status, source=SOURCE), field())
    assert any(issue.code == code and issue.severity == severity for issue in issues)
    assert all("source_shape" not in issue.public_dict() for issue in issues)


def test_membership_exact_uri_not_labels_prefix_or_payload_vocabulary() -> None:
    value = concept(status="resolved", source=SOURCE, vocabulary="urn:test:claimed")
    assert not validate_catalog_value(
        value, field(), vocabularies=evidence(complete=True, members=(URI,))
    )
    issues = validate_catalog_value(value, field(), vocabularies=evidence(complete=True))
    assert [issue.code for issue in issues] == [CatalogCode.VOCABULARY_MISMATCH]
    issues = validate_catalog_value(value, field(), vocabularies=evidence(complete=False))
    assert [issue.code for issue in issues] == [CatalogCode.VOCABULARY_UNVERIFIED]
    assert issues[0].severity == "Warning"


def test_profile_membership_uses_exact_uri_and_reports_field_path() -> None:
    graph = Graph()
    graph.add((URIRef(ROOT_URI), RDF.type, BF.Work))
    graph.add((URIRef(ROOT_URI), BF.language, URIRef(URI)))
    profile = load_legacy_profile(CONTROLLED_VOCABULARY_PROFILE_ID)

    def registry(*, complete: bool, members: tuple[str, ...] = ()) -> VocabularyRegistry:
        return VocabularyRegistry(
            vocabularies=(
                VocabularyDefinition(
                    uri=VOCABULARY,
                    release="test-release",
                    source=SOURCE,
                    members=members,
                    complete=complete,
                ),
            )
        )

    accepted = validate_profile_vocabulary_membership(
        graph, profile, registry(complete=True, members=(URI,)), operation="prevalidation"
    )
    assert accepted == ()

    mismatch = validate_profile_vocabulary_membership(
        graph, profile, registry(complete=True), operation="prevalidation"
    )
    assert len(mismatch) == 1
    assert mismatch[0].code == CatalogCode.VOCABULARY_MISMATCH
    assert mismatch[0].severity == "Violation"
    assert mismatch[0].field_ids == ("/language",)
    assert mismatch[0].path is not None and mismatch[0].path.iri == str(BF.language)
    assert mismatch[0].value == URI

    partial = validate_profile_vocabulary_membership(
        graph, profile, registry(complete=False), operation="prevalidation"
    )
    membership_warnings = [
        issue for issue in partial if issue.code == CatalogCode.VOCABULARY_UNVERIFIED
    ]
    assert len(membership_warnings) == 1
    assert membership_warnings[0].severity == "Warning"
    assert all(issue.severity == "Warning" for issue in partial)


def test_profile_membership_deprecated_and_unavailable_evidence_are_non_destructive() -> None:
    graph = Graph()
    graph.add((URIRef(ROOT_URI), RDF.type, BF.Work))
    graph.add((URIRef(ROOT_URI), BF.language, URIRef(URI)))
    profile = load_legacy_profile(CONTROLLED_VOCABULARY_PROFILE_ID)
    replacement = URI + "-replacement"
    evidence = VocabularyConceptEvidence(
        uri=URI,
        status="deprecated",
        source=SOURCE,
        replacement=replacement,
    )
    deprecated = VocabularyRegistry(
        vocabularies=(
            VocabularyDefinition(
                uri=VOCABULARY,
                release="test-release",
                source=SOURCE,
                concepts=(evidence,),
                complete=True,
            ),
        )
    )
    issues = validate_profile_vocabulary_membership(
        graph, profile, deprecated, operation="publish"
    )
    assert len(issues) == 1
    assert issues[0].code == CatalogCode.REFERENCE_DEPRECATED
    assert issues[0].severity == "Warning"
    assert issues[0].value == URI
    assert issues[0].parameters["replacement_uri"] == replacement

    unavailable = VocabularyRegistry(
        vocabularies=(
            VocabularyDefinition(
                uri=VOCABULARY,
                release="test-release",
                source=SOURCE,
                concepts=(
                    VocabularyConceptEvidence(uri=URI, status="unavailable"),
                ),
                complete=True,
            ),
        )
    )
    warnings = validate_profile_vocabulary_membership(
        graph, profile, unavailable, operation="publish"
    )
    assert warnings
    assert all(issue.severity == "Warning" for issue in warnings)


def test_profile_patch_membership_only_checks_new_uri_values() -> None:
    graph = Graph()
    graph.add((URIRef(ROOT_URI), RDF.type, BF.Work))
    graph.add((URIRef(ROOT_URI), BF.language, URIRef(URI)))
    previous = Graph()
    previous.add((URIRef(ROOT_URI), RDF.type, BF.Work))
    previous.add((URIRef(ROOT_URI), BF.language, URIRef(URI)))
    registry = VocabularyRegistry(
        vocabularies=(
            VocabularyDefinition(
                uri=VOCABULARY,
                release="test-release",
                source=SOURCE,
                complete=True,
            ),
        )
    )
    profile = load_legacy_profile(CONTROLLED_VOCABULARY_PROFILE_ID)
    assert not validate_profile_vocabulary_membership(
        graph,
        profile,
        registry,
        operation="update",
        previous_graph=previous,
    )


def test_profile_release_policy_preserves_v11_and_checks_only_touched_values() -> None:
    legacy = load_legacy_profile("urn:libris:profile:monograph:v1:1")
    current = load_legacy_profile(CONTROLLED_VOCABULARY_PROFILE_ID)
    assert legacy.release == "1.1"
    assert legacy.id == "urn:libris:profile:monograph:v1:1"
    assert current.release == "1.2"
    assert current.id == CONTROLLED_VOCABULARY_PROFILE_ID
    assert legacy.resources == current.resources

    graph = Graph()
    graph.add((URIRef(ROOT_URI), RDF.type, BF.Work))
    graph.add((URIRef(ROOT_URI), BF.language, URIRef(URI)))
    graph.add((URIRef(ROOT_URI), BF.language, URIRef(URI + "-new")))
    previous = Graph()
    previous.add((URIRef(ROOT_URI), RDF.type, BF.Work))
    previous.add((URIRef(ROOT_URI), BF.language, URIRef(URI)))
    registry = VocabularyRegistry(
        vocabularies=(
            VocabularyDefinition(
                uri=VOCABULARY,
                release="test-release",
                source=SOURCE,
                complete=True,
            ),
        )
    )
    issues = validate_profile_vocabulary_membership(
        graph,
        legacy,
        registry,
        operation="update",
        previous_graph=previous,
    )
    assert len(issues) == 1
    assert issues[0].code == CatalogCode.VOCABULARY_MISMATCH
    assert issues[0].value == URI + "-new"
    assert validate_profile_vocabulary_membership(
        graph, legacy, registry, operation="publish"
    ) == ()


def test_wrong_type_and_explicit_profile_enum() -> None:
    issues = validate_catalog_value(LiteralValue(kind="literal", value="Português"), field())
    assert issues[0].code == CatalogCode.VALUE
    payload = field().model_dump()
    payload["policy"] = CatalogPolicy(allowed_values=(IRIDefault(kind="iri", iri="urn:other"),))
    restricted = DeclarativeField.model_validate(payload)
    assert any(
        i.code == CatalogCode.PROFILE_POLICY for i in validate_catalog_value(concept(), restricted)
    )
    assert ReferenceValue(kind="iri").vocabulary is None
    with pytest.raises(ValidationError):
        ReferenceValue(kind="vocabulary")


@pytest.mark.parametrize(
    "outcome,reason",
    [
        ("temporary_failure", "unavailable"),
        ("permanent_failure", "not_found"),
        ("permanent_failure", "unsupported"),
        ("permanent_failure", "access_denied"),
    ],
)
def test_provider_failures_preserve_accepted_value_and_known_evidence(
    outcome: str,
    reason: str,
) -> None:
    original = concept(status="resolved", source=SOURCE, label=[{"value": "Catalogado"}])
    before = original.model_dump_json()
    prior = ResolutionSuccess(outcome="success", uri=URI, provenance=provenance(), concept=original)
    failure = ResolutionFailure.model_validate(
        {
            "outcome": outcome,
            "reason": reason,
            "uri": URI,
            "provenance": provenance(),
        }
    )
    provider = StubProvider(failure)
    enriched = asyncio.run(resolve_concept(original, provider, previous_success=prior))
    assert provider.uris == [URI]
    assert enriched.cataloged.status == "resolved"
    assert enriched.cataloged.model_dump_json() == before == original.model_dump_json()
    assert enriched.previous_success == prior
    assert resolution_diagnostics(failure)[0].severity == "Warning"
    assert EnrichedConcept.model_validate_json(enriched.model_dump_json()) == enriched


def test_success_deprecation_replacement_is_enrichment_not_identity_change() -> None:
    original = concept(label=[{"value": "Catalogado"}])
    result = ResolutionSuccess(
        outcome="success",
        uri=URI,
        provenance=provenance(),
        concept=concept(
            status="deprecated",
            source=SOURCE,
            replacement="urn:test:successor",
            label=[{"value": "Provider label", "language": "en"}],
        ),
    )
    enriched = asyncio.run(resolve_concept(original, StubProvider(result)))
    assert enriched.cataloged == original and enriched.cataloged.uri == URI
    assert isinstance(enriched.attempt, ResolutionSuccess)
    assert enriched.attempt.concept.replacement == "urn:test:successor"
    wrong = result.model_dump()
    wrong["uri"] = "urn:wrong"
    with pytest.raises(ValidationError):
        ResolutionSuccess.model_validate(wrong)
    wrong["concept"] = concept(uri="urn:wrong", status="resolved", source=SOURCE)
    with pytest.raises(ValidationError):
        asyncio.run(
            resolve_concept(original, StubProvider(ResolutionSuccess.model_validate(wrong)))
        )


def test_cache_policy_clock_ttls_and_failure_invariants() -> None:
    policy = ResolutionCachePolicy()
    success = ResolutionSuccess(
        outcome="success",
        uri=URI,
        provenance=provenance(),
        concept=concept(status="resolved", source=SOURCE),
    )
    assert policy.fresh(success, now=NOW + timedelta(seconds=86399))
    assert not policy.fresh(success, now=NOW + timedelta(days=1))
    assert not policy.fresh(success, now=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError):
        policy.fresh(success, now=datetime(2026, 10, 12))
    permanent = ResolutionFailure(
        outcome="permanent_failure", uri=URI, provenance=provenance(), reason="not_found"
    )
    assert policy.ttl(permanent) == 300
    transient = ResolutionFailure(
        outcome="temporary_failure",
        uri=URI,
        provenance=provenance(),
        reason="unavailable",
        retry_after_seconds=10,
    )
    assert policy.ttl(transient) == 10
    assert policy.usable_stale(success, transient, now=NOW + timedelta(days=2))
    assert not policy.usable_stale(success, permanent, now=NOW + timedelta(days=2))
    assert not policy.usable_stale(success, transient, now=NOW + timedelta(days=8))
    zero_retry = ResolutionFailure(
        outcome="temporary_failure",
        uri=URI,
        provenance=provenance(),
        reason="unavailable",
        retry_after_seconds=0,
    )
    assert policy.ttl(zero_retry) == 0
    with pytest.raises(ValidationError):
        ResolutionFailure(
            outcome="permanent_failure", uri=URI, provenance=provenance(), reason="unavailable"
        )
    with pytest.raises(ValidationError):
        ResolutionProvenance(provider="urn:p", source=SOURCE, retrieved_at=datetime(2026, 10, 12))


@pytest.mark.parametrize("format", ["json-ld", "turtle"])
def test_external_rdf_preserves_uri_without_publishing_enrichment(format: str) -> None:
    value = concept(
        status="deprecated",
        source=SOURCE,
        replacement="urn:test:successor",
        label=[{"value": "Português", "language": "pt"}],
        notation="por",
    )
    graph = catalog_value_to_graph(ROOT_URI, str(BF.language), value)
    assert len(graph) == 1 and graph.value(URIRef(ROOT_URI), BF.language) == URIRef(URI)
    recovered = parse_representation(serialize_graph(graph, format=format), format)
    assert isomorphic(graph, recovered)
    assert not list(recovered.triples((URIRef(URI), None, None)))
    assert not list(recovered.triples((None, OWL.sameAs, None)))


@pytest.mark.parametrize("format", ["json-ld", "turtle"])
def test_local_rdf_description_preserves_labels_notation_identity(format: str) -> None:
    value = local()
    graph = local_concept_to_graph(value)
    assert (URIRef(value.uri), RDF.type, SKOS.Concept) in graph
    assert (URIRef(value.uri), SKOS.notation, Literal("AMZ")) in graph
    assert Literal("Coleção amazônica", lang="pt") in graph.objects()
    assert Literal("Amazon collection", lang="en") in graph.objects()
    recovered = parse_representation(serialize_graph(graph, format=format), format)
    assert isomorphic(graph, recovered)
    assert not list(graph.triples((None, OWL.sameAs, None)))


@pytest.mark.parametrize(
    "datatype,value",
    [("integer", "42"), ("decimal", "12.5"), ("boolean", "true"), ("date", "2026-10-12")],
)
def test_typed_literal_round_trips(datatype: str, value: str) -> None:
    literal = LiteralValue(kind="literal", value=value, datatype=str(XSD[datatype]))
    graph = catalog_value_to_graph(ROOT_URI, "urn:test:value", literal)
    assert isinstance(graph.value(URIRef(ROOT_URI), URIRef("urn:test:value")), Literal)
    for format in ("json-ld", "turtle"):
        assert isomorphic(
            graph, parse_representation(serialize_graph(graph, format=format), format)
        )


def test_entity_resolution_reuses_stored_identity_and_rejects_pending() -> None:
    identity = ResourceIdentity(UUID(int=1), ROOT_URI)
    entity = EntityValue.model_validate(
        {
            "kind": "entity_reference",
            "reference": {
                "kind": "local",
                "entity_type": "Work",
                "internal_id": str(identity.internal_id),
            },
        }
    )
    with pytest.raises(RdfDocumentError):
        catalog_value_to_graph("urn:test:owner", str(BF.relatedTo), entity)
    resolved = ResolvedEntity(identity=identity, entity_type="Work")
    graph = catalog_value_to_graph(
        "urn:test:owner", str(BF.relatedTo), entity, references={identity.internal_id: resolved}
    )
    assert URIRef(ROOT_URI) in graph.objects()
    pending = EntityValue.model_validate(
        {
            "kind": "entity_reference",
            "reference": {
                "kind": "unreconciled",
                "entity_type": "Agent",
                "label": "Nome",
                "namespace": "urn:test:source",
                "key": "agent-1",
            },
        }
    )
    with pytest.raises(RdfDocumentError):
        catalog_value_to_graph(ROOT_URI, str(BF.agent), pending)


def test_historical_work_schema_and_shacl_stay_compatible() -> None:
    work = WorkRead.model_validate_json(
        (ROOT / "docs/examples/serialization/work.json").read_text()
    )
    payload = work.model_dump()
    payload["language"] = [concept().as_reference()]
    converted = WorkRead.model_validate(payload)
    graph = resource_to_graph(converted)
    assert (URIRef(work.uri), BF.language, URIRef(URI)) in graph
    profile = load_profile_registry().resolve("urn:libris:profile:work:2.0.0")
    context = ProfileValidationContext(profile_release=profile.release_id)
    assert validate_resolved_profile(graph, profile, context).conforms
    # SHACL confirms the IRI constraint, not membership/external availability.
    assert any(
        i.code == CatalogCode.VOCABULARY_UNVERIFIED
        for i in validate_catalog_value(concept(), field())
    )
    payload["language"] = [concept().model_dump()]
    with pytest.raises(ValidationError):
        WorkRead.model_validate(payload)


def test_schema_openapi_and_checked_examples() -> None:
    schema_path = ROOT / (
        "apps/api/src/libris/modules/bibliographic/resources/controlled-values-v1/"
        "catalog-value-1.0.schema.json"
    )
    assert json.loads(schema_path.read_text()) == CatalogValueDocument.model_json_schema()
    app = FastAPI()

    @app.post("/test-only", response_model=CatalogValueDocument)
    def echo(document: CatalogValueDocument) -> CatalogValueDocument:
        return document

    schema = app.openapi()["components"]["schemas"]["CatalogValueDocument"]
    assert schema["properties"]["value"]["discriminator"]["propertyName"] == "kind"
    for name in ("external", "local"):
        doc = CatalogValueDocument.model_validate_json((EXAMPLES / f"{name}.json").read_text())
        graph = (
            local_concept_to_graph(doc.value)
            if isinstance(doc.value, LocalConcept)
            else catalog_value_to_graph(ROOT_URI, str(BF.language), doc.value)
        )
        for format, suffix in (("json-ld", "jsonld"), ("turtle", "ttl")):
            assert isomorphic(
                graph, parse_representation((EXAMPLES / f"{name}.{suffix}").read_text(), format)
            )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_controlled_value_artifacts.py"), "--check"],
        check=True,
    )


@pytest.mark.parametrize("status", ["resolved", "invalid"])
def test_provider_definitive_evidence_does_not_rewrite_original(status: str) -> None:
    original = concept()
    result = ResolutionSuccess(
        outcome="success",
        uri=URI,
        provenance=provenance(),
        concept=concept(status=status, source=SOURCE),
    )
    enriched = asyncio.run(resolve_concept(original, StubProvider(result)))
    assert enriched.cataloged.status == "unverified"
    assert isinstance(enriched.attempt, ResolutionSuccess)
    assert enriched.attempt.concept.status == status
    result.concept.label.append(LocalizedText(value="Mutated"))
    assert enriched.attempt.concept.label == []


def test_membership_configuration_is_versioned_and_rejects_duplicate_evidence() -> None:
    with pytest.raises(ValidationError):
        evidence(complete=True, members=(URI, URI))
    entry = evidence(complete=True, members=(URI,)).vocabularies[0]
    with pytest.raises(ValidationError):
        VocabularyRegistry(vocabularies=(entry, entry))
    assert evidence(complete=False, members=(URI,)).membership(URI, VOCABULARY) is True
    assert evidence(complete=True).membership(URI, "urn:test:unknown") is None


def test_resolution_and_registry_generated_schemas() -> None:
    models = {
        "enriched-concept": EnrichedConcept,
        "cache-policy": ResolutionCachePolicy,
        "vocabulary-registry": VocabularyRegistry,
    }
    for name, model in models.items():
        path = ROOT / (
            "apps/api/src/libris/modules/bibliographic/resources/controlled-values-v1/"
            f"{name}-1.0.schema.json"
        )
        assert json.loads(path.read_text()) == model.model_json_schema()
