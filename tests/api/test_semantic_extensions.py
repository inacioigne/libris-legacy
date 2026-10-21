"""Issue #25: governed extensions and integral RDF, without remote services."""

import asyncio
import json
import socket
from functools import cache
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from libris.modules.bibliographic.application.service import (
    BibliographicService,
    prepare_document,
)
from libris.modules.bibliographic.domain.editorial import EditorialRecord
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    Revision,
)
from libris.modules.bibliographic.domain.profile_contract import (
    DeclarativeField,
    DeclarativeResource,
    ProfileDefinition,
    ProfileValidationContext,
    ResolvedProfile,
)
from libris.modules.bibliographic.domain.profile_registry import (
    ProfileError,
    ProfileRegistry,
    SemanticRegistry,
    ShapeRegistry,
    compare_profiles,
)
from libris.modules.bibliographic.infrastructure import profiles as loader
from libris.modules.bibliographic.infrastructure import validation
from libris.modules.bibliographic.infrastructure.ownership import check_snapshot
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    RdfDocumentError,
    parse_document,
)
from libris.modules.bibliographic.infrastructure.serialization import (
    local_context,
    parse_representation,
    serialize_graph,
)
from libris.modules.bibliographic.infrastructure.validation import (
    INSTITUTIONAL_PROFILE_ID,
    validate_resolved_profile,
)
from libris.modules.identity.application.authorization import TechnicalContext
from libris.modules.identity.domain.authorization import Operation
from libris.modules.identity.domain.permissions import Permission as ContextPermission
from pydantic import ValidationError
from rdflib import Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, OWL, PROV, RDF, SH, XSD

TEST_CONTEXT = TechnicalContext(
    "service:regression-fixture",
    frozenset(
        {
            ContextPermission.BIBLIOGRAPHIC_CREATE,
            ContextPermission.BIBLIOGRAPHIC_UPDATE,
            ContextPermission.BIBLIOGRAPHIC_READ,
            ContextPermission.AUTHORITY_READ,
            ContextPermission.AUTHORITY_MANAGE,
            ContextPermission.METADATA_REVISION_READ,
        }
    ),
    frozenset(
        {
            Operation.BIBLIOGRAPHIC_CREATE,
            Operation.BIBLIOGRAPHIC_UPDATE,
            Operation.AUTHORITY_MANAGE,
            Operation.METADATA_REVISION_READ,
            Operation.BIBLIOGRAPHIC_READ_EDITORIAL,
            Operation.AUTHORITY_READ_EDITORIAL,
        }
    ),
    "pytest:isolated-regression",
)


ROOT = Path(__file__).parents[2]
ARTIFACTS = ROOT / "apps/api/src/libris/modules/bibliographic/resources/profiles-v2"
FIXTURES = ROOT / "tests/fixtures/bibliographic/extensions"
RELEASE = "urn:libris:profile:work-extensions:2.0.0"
WORK = "https://example.org/works/123"


@cache
def profile() -> ResolvedProfile:
    return loader.load_profile_registry().resolve(RELEASE)


def graph() -> Graph:
    return parse_representation((FIXTURES / "work.ttl").read_text(), "turtle")


def semantics() -> dict:
    return json.loads((ARTIFACTS / "semantics-v2.json").read_text())


def extension_field() -> DeclarativeField:
    return next(f for r in profile().resources for f in r.fields if f.name == "abstract")


def work_resource() -> DeclarativeResource:
    return next(r for r in profile().resources if r.rdf_class == str(BF.Work))


def validate(data: Graph, selected: ResolvedProfile | None = None) -> validation.ValidationReport:
    selected = selected or profile()
    return validate_resolved_profile(
        data, selected, ProfileValidationContext(profile_release=selected.release_id)
    )


@pytest.mark.parametrize("mutation", ["prefix", "iri", "overlap", "invalid", "bf_alias"])
def test_namespace_conflicts(mutation: str) -> None:
    data = semantics()
    entry = dict(data["namespaces"][0])
    if mutation == "prefix":
        entry["iri"] = "urn:other:"
    elif mutation == "iri":
        entry["prefix"] = "other"
    elif mutation == "overlap":
        entry.update(prefix="parent", iri="http://id.loc.gov/")
    elif mutation == "invalid":
        entry.update(prefix="invalid", iri="relative/")
    else:
        data["namespaces"][0]["prefix"] = "other"
        entry = None
    if entry:
        data["namespaces"].append(entry)
    with pytest.raises(ValidationError):
        SemanticRegistry.model_validate(data)


@pytest.mark.parametrize("key", ["label", "description", "maintainer", "inclusion"])
def test_governed_namespace_requires_traceability(key: str) -> None:
    data = semantics()
    data["namespaces"][0].pop(key)
    with pytest.raises(ValidationError):
        SemanticRegistry.model_validate(data)


def test_registered_terms_and_origin_without_new_entities() -> None:
    registry = loader.load_semantic_registry("2.0")
    assert registry.namespace_for(str(DCTERMS.abstract)).prefix == "dcterms"
    assert registry.check_class(str(PROV.Entity)).auxiliary
    assert registry.check_class(str(BF.Work)).entity.value == "Work"
    registry.check_field(work_resource(), extension_field())
    data = semantics()
    data["classes"][-1].update(auxiliary=False, entity="Work")
    with pytest.raises(ValidationError):
        SemanticRegistry.model_validate(data)


@pytest.mark.parametrize("target", ["namespace", "approval", "property", "class"])
def test_known_but_unauthorized(target: str) -> None:
    data = semantics()
    if target in {"namespace", "approval"}:
        entry = next(n for n in data["namespaces"] if n["prefix"] == "dcterms")
        entry["status" if target == "namespace" else "approval"] = (
            "review_required" if target == "namespace" else "pending"
        )
        code = "extension_review_required"
    elif target == "property":
        data["properties"][-1]["status"] = "review_required"
        code = "unsupported_property"
    else:
        data["classes"][-1]["status"] = "review_required"
        code = "unsupported_class"
    registry = SemanticRegistry.model_validate(data)
    with pytest.raises(ProfileError) as error:
        if target == "class":
            registry.check_class(str(PROV.Entity))
        else:
            registry.check_field(work_resource(), extension_field())
    assert error.value.code == code


@pytest.mark.parametrize(
    "change,code",
    [
        ("unknown", "unknown_property"),
        ("namespace", "namespace_mismatch"),
        ("value", "incompatible_property"),
        ("source", "incompatible_property"),
    ],
)
def test_external_field_contract_rejections(change: str, code: str) -> None:
    field = extension_field().model_dump()
    resource = work_resource()
    if change == "unknown":
        field["property"] = "http://purl.org/dc/terms/unknown"
    elif change == "namespace":
        field["namespace"] = "bf"
    elif change == "value":
        field["value"] = {"kind": "typed", "datatypes": [str(XSD.integer)]}
        field["presentation"]["input"] = "number"
    else:
        resource = DeclarativeResource.model_validate(
            {
                **resource.model_dump(),
                "rdf_class": str(BF.Instance),
                "entity": "Instance",
            }
        )
    with pytest.raises(ProfileError) as error:
        loader.load_semantic_registry("2.0").check_field(
            resource, DeclarativeField.model_validate(field)
        )
    assert error.value.code == code


@pytest.mark.parametrize("change", ["namespace", "source", "target", "class_namespace"])
def test_invalid_term_metadata(change: str) -> None:
    data = semantics()
    if change == "namespace":
        data["properties"][-1]["namespace"] = "bf"
    elif change == "class_namespace":
        data["classes"][-1]["iri"] = "urn:unregistered:Entity"
    else:
        data["properties"][-1]["sources" if change == "source" else "target_classes"] = [
            "urn:unknown:Class"
        ]
    with pytest.raises(ValidationError):
        SemanticRegistry.model_validate(data)


def test_historical_registry_and_profile_are_not_reinterpreted() -> None:
    registry = loader.load_profile_registry()
    old = registry.resolve("urn:libris:profile:work:2.0.0")
    mixed = registry.resolve(RELEASE)
    assert not any(f.property == str(DCTERMS.abstract) for r in old.resources for f in r.fields)
    assert mixed.lineage == (*old.lineage, RELEASE)
    assert extension_field().policy.min_count == 0
    assert extension_field().presentation.input == "textarea"
    assert extension_field().label == {"pt": "Resumo", "en": "Abstract"}
    with pytest.raises(ProfileError, match="registrada"):
        loader.load_semantic_registry("1.0").check_field(work_resource(), extension_field())
    loader.load_semantic_registry("2.0").check_extension_of(loader.load_semantic_registry("1.0"))
    assert all(
        change.compatible
        for change in compare_profiles(old.model_copy(update={"id": mixed.id}), mixed)
    )


def test_unknown_releases_and_pure_bibframe() -> None:
    with pytest.raises(ProfileError) as error:
        loader.load_semantic_registry("99.0")
    assert error.value.code == "unknown_semantics"
    with pytest.raises(ProfileError):
        loader.load_profile_registry().resolve("urn:unknown:release")
    data = graph()
    for predicate in (DCTERMS.abstract, DCTERMS.created, DCTERMS.provenance):
        data.remove((URIRef(WORK), predicate, None))
    # Unreachable auxiliary is removed for an owned pure-BIBFRAME snapshot.
    data.remove((None, RDF.type, PROV.Entity))
    data.remove((None, PROV.value, None))
    assert validate(data).conforms
    assert check_snapshot(data, URIRef(WORK)) == BF.Work


@pytest.mark.parametrize("release", ["1.0", "2.0"])
def test_semantic_release_tampering_rejected(release: str, monkeypatch: pytest.MonkeyPatch) -> None:
    read = loader._read

    def changed(name: str) -> str:
        document = read(name)
        return document + " " if name == loader.SEMANTIC_RELEASE_FILES[release] else document

    monkeypatch.setattr(loader, "_read", changed)
    with pytest.raises(ProfileError) as error:
        loader.load_semantic_registry(release)
    assert error.value.code == "immutable_semantics"


@pytest.mark.parametrize("change", ["namespace", "class", "property"])
def test_semantic_redefinition_rejected(change: str) -> None:
    data = semantics()
    if change == "namespace":
        data["namespaces"][1]["ontology"] = "urn:replacement:ontology"
    elif change == "class":
        data["classes"] = [c for c in data["classes"] if c["iri"] != str(BF.ShelfMark)]
        for prop in data["properties"]:
            prop["sources"] = [c for c in prop["sources"] if c != str(BF.ShelfMark)]
            prop["target_classes"] = [
                c for c in prop.get("target_classes", []) if c != str(BF.ShelfMark)
            ]
    else:
        data["properties"][0]["maximum"] = 1
    current = SemanticRegistry.model_validate(data)
    with pytest.raises(ProfileError) as error:
        current.check_extension_of(loader.load_semantic_registry("1.0"))
    assert error.value.code == "semantic_override"


@pytest.mark.parametrize("status", ["deprecated", "review_required"])
def test_deprecated_term_cannot_be_new_editable_field(status: str) -> None:
    data = semantics()
    data["properties"][-1]["status"] = status
    with pytest.raises(ProfileError) as error:
        SemanticRegistry.model_validate(data).check_field(work_resource(), extension_field())
    assert error.value.code == "unsupported_property"
    # Its RDF data remains exportable, regardless of future editing authorization.
    assert isomorphic(
        graph(),
        parse_representation(serialize_graph(graph(), format="json-ld"), "json-ld"),
    )


def test_required_extension_inherits_and_composes_without_ontology_cardinality() -> None:
    registry = loader.load_profile_registry()
    base = registry.definition(RELEASE)
    field = extension_field().model_dump()
    field.update(overrides=True)
    field["policy"].update(min_count=1, required=True)
    child = ProfileDefinition.model_validate(
        {
            **base.model_dump(),
            "release_id": "urn:libris:profile:work-extensions:2.1.0",
            "release": "2.1.0",
            "base_release": RELEASE,
            "resources": [{**work_resource().model_dump(), "fields": [field]}],
        }
    )
    definitions = [registry.definition(release) for release in profile().lineage] + [child]
    resolved = ProfileRegistry(
        definitions,
        loader.load_semantic_registry("1.0"),
        ShapeRegistry.model_validate_json((ARTIFACTS / "shapes-v1.json").read_text()),
        semantics_by_release={
            RELEASE: loader.load_semantic_registry("2.0"),
            child.release_id: loader.load_semantic_registry("2.0"),
        },
    ).resolve(child.release_id)
    data = graph()
    data.remove((URIRef(WORK), DCTERMS.abstract, None))
    assert validate(data).conforms
    assert not validate(data, resolved).conforms
    assert not all(change.compatible for change in compare_profiles(profile(), resolved))
    assert (
        next(
            p
            for p in loader.load_semantic_registry("2.0").properties
            if p.iri == str(DCTERMS.abstract)
        ).minimum
        == 0
    )


@pytest.mark.parametrize("case", ["cardinality", "datatype", "node", "language", "wrong_title"])
def test_shacl_external_policy_rejections(case: str) -> None:
    data = graph()
    root = URIRef(WORK)
    if case == "cardinality":
        data.add((root, DCTERMS.abstract, Literal("English", lang="en")))
        data.add((root, DCTERMS.abstract, Literal("Français", lang="fr")))
    elif case == "datatype":
        data.set((root, DCTERMS.abstract, Literal(123)))
    elif case == "node":
        data.set((root, DCTERMS.abstract, URIRef("urn:abstract:reference")))
    elif case == "language":
        data.add((root, DCTERMS.abstract, Literal("Outro resumo", lang="pt")))
    else:
        title = next(data.objects(root, BF.title))
        data.remove((title, RDF.type, BF.Title))
        data.add((title, RDF.type, BF.Note))
    before = set(data)
    result = validate(data)
    assert not result.conforms
    assert any(issue.severity == str(SH.Violation) for issue in result.issues)
    assert set(data) == before


@pytest.mark.parametrize("severity", [SH.Warning, SH.Info])
def test_extension_warning_and_info_nonblocking(
    severity: URIRef, monkeypatch: pytest.MonkeyPatch
) -> None:
    load = validation._load_shapes

    def supplemental(names: tuple[str, ...]) -> Graph:
        shapes = load(names)
        rule = URIRef("urn:libris:test:extension-rule")
        for triple in [
            (rule, RDF.type, SH.PropertyShape),
            (rule, SH.targetClass, BF.Work),
            (rule, SH.path, DCTERMS.abstract),
            (rule, SH.minCount, Literal(2)),
            (rule, SH.severity, severity),
        ]:
            shapes.add(triple)
        return shapes

    monkeypatch.setattr(validation, "_load_shapes", supplemental)
    result = validate(graph())
    assert result.conforms
    assert any(issue.severity == str(severity) for issue in result.issues)


@pytest.mark.parametrize("format", ["json-ld", "turtle"])
def test_examples_roundtrip_preserve_all_terms(format: str) -> None:
    data = graph()
    before, bindings = set(data), tuple(data.namespaces())
    document = serialize_graph(data, format=format, root=WORK)
    recovered = parse_representation(document, format)
    assert isomorphic(data, recovered)
    assert set(data) == before and tuple(data.namespaces()) == bindings
    assert (
        URIRef(WORK),
        DCTERMS.abstract,
        Literal("Descrição complementar da obra.", lang="pt"),
    ) in recovered
    assert (
        URIRef(WORK),
        DCTERMS.created,
        Literal("2026-10-12", datatype=XSD.date),
    ) in recovered
    assert any(recovered.subjects(RDF.type, PROV.Entity))
    assert validate(recovered).conforms
    assert not any(data.triples((None, OWL.sameAs, None)))
    assert not any(data.triples((None, OWL.equivalentProperty, None)))
    paired = parse_representation((FIXTURES / "work.jsonld").read_text(), "json-ld")
    assert isomorphic(paired, recovered)


@pytest.mark.parametrize(
    "context",
    [
        "https://example.org/context",
        {"@import": "https://example.org/context"},
        {"bf": "urn:replacement:"},
        {"evil": "urn:evil:"},
    ],
)
def test_untrusted_context_rejected(context: object, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    with pytest.raises(RdfDocumentError):
        parse_representation(
            json.dumps({"@context": context, "@id": WORK, "@type": str(BF.Work)}),
            "json-ld",
        )


def test_data_preservation_does_not_authorize_editing() -> None:
    data = graph()
    assert validate(data).conforms
    assert not any(
        f.property == str(DCTERMS.created) for r in profile().resources for f in r.fields
    )
    with pytest.raises(ProfileError) as error:
        loader.load_semantic_registry("2.0").check_field(
            work_resource(),
            DeclarativeField.model_validate(
                {**extension_field().model_dump(), "property": str(DCTERMS.created)}
            ),
        )
    assert error.value.code == "unknown_property"
    assert local_context()["bf"] == str(BF)


def test_owned_snapshot_and_revision_preserve_extension() -> None:
    data = graph()
    identity = ResourceIdentity.create("https://example.org/")
    root = URIRef(identity.uri)
    for predicate, value in list(data.predicate_objects(URIRef(WORK))):
        data.add((root, predicate, value))
    data.remove((URIRef(WORK), None, None))
    initial = prepare_document(data, identity.uri, INSTITUTIONAL_PROFILE_ID)
    previous_graph = parse_document(json.dumps(initial), "json-ld")
    assert isomorphic(data, previous_graph)
    # The real service's revision path is exercised with a mocked repository, not SQLite.
    from datetime import UTC, datetime

    revision = Revision(
        identity,
        1,
        datetime.now(UTC),
        Provenance("fixture:local", "test"),
        INSTITUTIONAL_PROFILE_ID,
    )
    sessions = MagicMock()
    session = AsyncMock()
    sessions.return_value.__aenter__.return_value = session
    session.begin = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock()
    session.begin.return_value.__aexit__ = AsyncMock()
    service = BibliographicService(sessions)
    service.store = MagicMock()
    service.store.get = AsyncMock(return_value=(revision, initial))
    service.store.advance = AsyncMock(return_value=identity.uri)
    service.store.save = AsyncMock()
    service.audit.append = AsyncMock()
    service.editorial.lock_resource = AsyncMock()
    service.editorial.initialize = AsyncMock(return_value=EditorialRecord(identity))
    service.editorial.save = AsyncMock()
    title = next(data.objects(root, BF.title))
    data.set((title, BF.mainTitle, Literal("Título atualizado", lang="pt")))
    result = asyncio.run(
        service.revise(
            identity.internal_id,
            1,
            data,
            Provenance("fixture:local", "update"),
            context=TEST_CONTEXT,
        )
    )
    observed = service.audit.append.call_args.args[1]
    assert observed.event_type.value == "record.revised"
    assert observed.identity == identity and observed.revision_number == 2
    saved = service.store.save.call_args.args[2]
    recovered = parse_document(json.dumps(saved), "json-ld")
    assert result.profile == INSTITUTIONAL_PROFILE_ID and result.number == 2
    assert isomorphic(data, recovered)
    assert set(recovered.objects(root, DCTERMS.abstract)) == set(
        previous_graph.objects(root, DCTERMS.abstract)
    )
    assert (
        title,
        BF.mainTitle,
        Literal("Exemplo de obra", lang="pt"),
    ) in previous_graph


def test_registry_association_survives_lifecycle_and_inheritance() -> None:
    registry = loader.load_profile_registry()
    assert registry.semantic_registry_for(RELEASE).version == "2.0"
    assert registry.semantic_registry_for("urn:libris:profile:work:2.0.0").version == "1.0"
    published = registry.transition_release(RELEASE, "published")
    assert published.semantic_registry_for(RELEASE).version == "2.0"
    child = ProfileDefinition.model_validate(
        {
            **registry.definition(RELEASE).model_dump(),
            "release_id": "urn:libris:profile:work-extensions:2.2.0",
            "release": "2.2.0",
            "base_release": RELEASE,
            "resources": (),
        }
    )
    extended = published.add_release(child)
    assert extended.semantic_registry_for(child.release_id).version == "2.0"
    assert any(
        f.property == str(DCTERMS.abstract)
        for r in extended.resolve(child.release_id).resources
        for f in r.fields
    )
    detached = extended.semantic_registry_for(child.release_id)
    detached.namespaces[0].label["pt"] = "Alteração local"
    assert (
        extended.semantic_registry_for(child.release_id).namespaces[0].label["pt"]
        != "Alteração local"
    )


@pytest.mark.parametrize("change", ["unknown_release", "mismatched_profile", "path"])
def test_invalid_semantic_association_rejected(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    read = loader._read

    def changed(name: str) -> str:
        document = read(name)
        if name != "profiles-v2/semantic-profile-releases.json":
            return document
        entries = json.loads(document)
        if change == "unknown_release":
            entries[0]["semantic_release"] = "99.0"
        elif change == "mismatched_profile":
            entries[0]["profile_release"] = "urn:unknown:profile"
        else:
            entries[0]["profile_file"] = "../../outside.json"
        return json.dumps(entries)

    monkeypatch.setattr(loader, "_read", changed)
    with pytest.raises((ProfileError, ValidationError)):
        loader.load_profile_registry()


@pytest.mark.parametrize("change", ["ontology", "identity"])
def test_standalone_bibframe_override_rejected(change: str) -> None:
    data = semantics()
    if change == "ontology":
        data["namespaces"][0]["ontology"] = "urn:replacement:ontology"
    else:
        next(c for c in data["classes"] if c["iri"] == str(BF.Work))["entity"] = "Item"
    with pytest.raises(ValidationError):
        SemanticRegistry.model_validate(data)


def test_unknown_and_unapproved_classes_are_not_profile_resources() -> None:
    registry = loader.load_semantic_registry("2.0")
    with pytest.raises(ProfileError) as error:
        registry.check_class("http://www.w3.org/ns/prov#Unregistered")
    assert error.value.code == "unknown_class"
    data = semantics()
    next(c for c in data["classes"] if c["iri"] == str(BF.Title))["status"] = "review_required"
    declaration = loader.load_profile_registry().definition("urn:libris:profile:core:2.0.0")
    with pytest.raises(ProfileError) as error:
        ProfileRegistry(
            [declaration],
            SemanticRegistry.model_validate(data),
            ShapeRegistry.model_validate_json((ARTIFACTS / "shapes-v1.json").read_text()),
        )
    assert error.value.code == "unsupported_class"
