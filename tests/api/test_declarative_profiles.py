"""Declarative contract/resolution, without database, network or production routes."""

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi import FastAPI
from libris.modules.bibliographic.domain.profile_contract import (
    XSD,
    CatalogPolicy,
    DeclarativeField,
    ProfileDefinition,
    ProfileValidationContext,
    ResolvedProfile,
    SnapshotReference,
)
from libris.modules.bibliographic.domain.profile_registry import (
    ProfileError,
    ProfileRegistry,
    SemanticRegistry,
    ShapeRegistry,
    compare_profiles,
)
from libris.modules.bibliographic.domain.profiles import (
    CatalogProfile,
    ProfileField,
    ProfileResource,
)
from libris.modules.bibliographic.domain.relationships import BF, EntityType
from libris.modules.bibliographic.infrastructure.profiles import (
    LEGACY_PROFILES,
    load_legacy_profile,
    load_profile_registry,
)
from libris.modules.bibliographic.infrastructure.rdf import parse_document
from libris.modules.bibliographic.infrastructure.validation import (
    SHAPE_RELEASE_FILES,
    ValidationIssue,
    ValidationReport,
    correlate_validation_issues,
    validate_profile,
    validate_resolved_profile,
)
from pydantic import ValidationError
from rdflib import Graph
from rdflib.namespace import SH

ROOT = Path(__file__).parents[2]
RESOURCES = files("libris.modules.bibliographic").joinpath("resources")
V2 = RESOURCES.joinpath("profiles-v2")
CORE = "urn:libris:profile:core:2.0.0"
SHAPES = "urn:libris:shapes:institutional"
INSTITUTION = "urn:libris:institution:example"
MANIFEST: list[str] = json.loads(V2.joinpath("manifest.json").read_text())


def data(name: str = "core-2.0.0.json") -> dict:
    return json.loads(V2.joinpath(name).read_text())


def definitions() -> list[ProfileDefinition]:
    return [ProfileDefinition.model_validate(data(name)) for name in MANIFEST]


def registry(
    entries: list[ProfileDefinition] | None = None, *, used: tuple[str, ...] = ()
) -> ProfileRegistry:
    return ProfileRegistry(
        entries if entries is not None else definitions(),
        SemanticRegistry.model_validate_json(
            V2.joinpath("semantics-v1.json").read_text()
        ),
        ShapeRegistry.model_validate_json(V2.joinpath("shapes-v1.json").read_text()),
        used_releases=used,
    )


def resource(payload: dict, rdf_class: str = BF + "Work") -> dict:
    return next(r for r in payload["resources"] if r["rdf_class"] == rdf_class)


def field(payload: dict, name: str = "title", rdf_class: str = BF + "Work") -> dict:
    return next(f for f in resource(payload, rdf_class)["fields"] if f["name"] == name)


def altered(payload: dict, **updates: object) -> ProfileDefinition:
    payload = copy.deepcopy(payload)
    payload.update(updates)
    return ProfileDefinition.model_validate(payload)


def override(name: str = "title") -> dict:
    payload = data("work-2.0.0.json")
    changed = copy.deepcopy(field(data(), name))
    changed["overrides"] = True
    payload["resources"] = [
        {
            "rdf_class": BF + "Work",
            "entity": "Work",
            "label": {"pt": "Obra"},
            "fields": [changed],
        }
    ]
    return payload


@pytest.mark.parametrize("name", MANIFEST)
def test_examples_roundtrip_and_resolve(name: str) -> None:
    definition = ProfileDefinition.model_validate(data(name))
    assert (
        ProfileDefinition.model_validate_json(definition.model_dump_json())
        == definition
    )
    resolved = load_profile_registry().resolve(definition.release_id)
    assert ResolvedProfile.model_validate_json(resolved.model_dump_json()) == resolved
    assert resolved.lineage[-1] == definition.release_id
    assert all(not f.overrides for r in resolved.resources for f in r.fields)
    assert all(
        f.property.startswith(("http:", "https:"))
        for r in resolved.resources
        for f in r.fields
    )


@pytest.mark.parametrize("entity", list(EntityType))
def test_five_entities_selection_and_no_python_subclasses(entity: EntityType) -> None:
    reg = load_profile_registry()
    requested = f"urn:libris:profile:{entity.value.lower()}:2.0.0"
    resolved = reg.select(entity, release_id=requested, statuses=("candidate",))
    assert resolved.applies_to == (entity,)
    assert reg.resolve(requested).lineage == (CORE, requested)
    assert reg.definition(requested).resources == ()


def test_sections_and_canonical_auxiliaries() -> None:
    resolved = load_profile_registry().resolve(CORE)
    classes = {r.rdf_class: r for r in resolved.resources}
    for name in ("Title", "Contribution", "Identifier", "Publication"):
        assert classes[BF + name].entity is None
        assert classes[BF + name].fields
    for r in resolved.resources:
        assert r.sections[0].id == "description"
        assert r.groups[0].section == "description"
    work = classes[BF + "Work"]
    assert next(f for f in work.fields if f.name == "title").value.kind == "structured"
    assert next(f for f in work.fields if f.name == "subject").value.kind == "entity"
    assert (
        next(
            f for f in classes[BF + "Title"].fields if f.name == "mainTitle"
        ).value.kind
        == "text"
    )


def test_new_form_required_does_not_change_semantic_cardinality() -> None:
    payload = data()
    optional = field(payload, "language")
    optional["presentation"]["required_in_form"] = True
    mandatory = field(payload)
    mandatory["presentation"]["required_in_form"] = False
    resolved = registry([ProfileDefinition.model_validate(payload)]).resolve(CORE)
    work = next(r for r in resolved.resources if r.entity == EntityType.WORK)
    language = next(f for f in work.fields if f.name == "language")
    title = next(f for f in work.fields if f.name == "title")
    assert language.policy.min_count == 0 and language.presentation.required_in_form
    assert title.policy.min_count == 1 and not title.presentation.required_in_form


@pytest.mark.parametrize(
    "iri",
    ["relative", "http://", "https://x/a b", "urn:", "https://x/%zz", "https://x/<y>"],
)
def test_invalid_iris(iri: str) -> None:
    payload = data()
    field(payload)["property"] = iri
    with pytest.raises(ValidationError):
        ProfileDefinition.model_validate(payload)


@pytest.mark.parametrize(
    "key,value",
    [
        ("name", "1bad"),
        ("namespace", ""),
        ("label", {"pt_BR": "inválido"}),
        ("label", {"pt": ""}),
        ("value", {"kind": "text", "node_kind": "IRI"}),
        ("value", {"kind": "entity", "target_entities": ["Book"]}),
        ("value", {"kind": "text", "language": {"enabled": False, "required": True}}),
        (
            "value",
            {"kind": "text", "language": {"enabled": True, "allowed": ["pt", "PT"]}},
        ),
        ("value", {"kind": "typed", "datatypes": ["urn:datatype:unknown"]}),
    ],
)
def test_invalid_field_structure(key: str, value: object) -> None:
    payload = data()
    field(payload)[key] = value
    with pytest.raises(ValidationError):
        ProfileDefinition.model_validate(payload)


@pytest.mark.parametrize(
    "policy",
    [
        {"min_count": -1},
        {"max_count": 0},
        {"min_count": 2, "max_count": 1, "repeatable": False},
        {"max_count": 1, "repeatable": True},
        {"min_count": 0, "required": True},
        {"min_count": True},
        {"max_count": "1"},
    ],
)
def test_invalid_cardinalities(policy: dict) -> None:
    with pytest.raises(ValidationError):
        CatalogPolicy.model_validate(policy)


@pytest.mark.parametrize(
    "duplicate", ["name", "property", "class", "section", "group", "entity"]
)
def test_duplicate_identifiers(duplicate: str) -> None:
    payload = data()
    work = resource(payload)
    if duplicate == "class":
        payload["resources"].append(copy.deepcopy(work))
    elif duplicate == "entity":
        payload["applies_to"].append("Work")
    elif duplicate in {"section", "group"}:
        work[duplicate + "s"].append(copy.deepcopy(work[duplicate + "s"][0]))
    else:
        work["fields"][1][duplicate] = work["fields"][0][duplicate]
    with pytest.raises(ValidationError):
        ProfileDefinition.model_validate(payload)


@pytest.mark.parametrize(
    "case,code",
    [
        ("namespace", "unknown_namespace"),
        ("alias", "namespace_mismatch"),
        ("unknown_property", "unknown_property"),
        ("origin", "incompatible_property"),
        ("class", "incompatible_class"),
        ("target", "incompatible_target"),
        ("derived", "derived_mismatch"),
        ("minimum", "domain_constraint"),
        ("missing_structure", "missing_structure"),
        ("dependency", "unknown_dependency"),
        ("section", "unknown_section"),
        ("group", "unknown_group"),
        ("shapes", "unknown_shapes"),
        ("context", "incompatible_shapes"),
        ("constraint", "unknown_constraint"),
        ("value_kind", "incompatible_property"),
    ],
)
def test_invalid_semantic_configuration(case: str, code: str) -> None:
    payload = data()
    if case == "namespace":
        field(payload)["property"] = "https://unregistered.example/title"
    elif case == "alias":
        field(payload)["namespace"] = "skos"
    elif case == "unknown_property":
        field(payload)["property"] = BF + "inventedProperty"
    elif case == "origin":
        field(payload)["property"] = BF + "editionStatement"
    elif case == "class":
        resource(payload)["rdf_class"] = BF + "Imaginary"
    elif case == "target":
        field(payload, "subject")["value"]["target_entities"] = ["Item"]
    elif case == "derived":
        field(payload, "hasInstance")["derived"] = False
    elif case == "minimum":
        field(payload, "instanceOf", BF + "Instance")["policy"].update(
            max_count=2, repeatable=True
        )
    elif case == "missing_structure":
        payload["resources"] = [
            r for r in payload["resources"] if r["rdf_class"] != BF + "Title"
        ]
    elif case == "dependency":
        field(payload)["presentation"]["conditions"] = [
            {"field": "missing", "operator": "present", "effect": "visible"}
        ]
    elif case == "section":
        field(payload)["presentation"]["section"] = "missing"
    elif case == "group":
        field(payload)["presentation"]["group"] = "missing"
    elif case == "shapes":
        payload["validation"][0]["release"] = "99.0"
    elif case == "context":
        payload["validation"][0] = {
            "id": "urn:libris:shapes:experimental",
            "release": "1.0",
            "context": "composed",
        }
    elif case == "constraint":
        field(payload)["validation"][0]["property_shape"] = "urn:shape:missing"
    elif case == "value_kind":
        field(payload)["value"] = {"kind": "text"}
        field(payload)["presentation"]["input"] = "text"
    with pytest.raises(ProfileError) as error:
        registry([ProfileDefinition.model_validate(payload)])
    assert error.value.code == code


def test_condition_validation_and_cycle() -> None:
    payload = data()
    field(payload)["presentation"]["conditions"] = [
        {"field": "language", "operator": "present", "effect": "visible"}
    ]
    registry([ProfileDefinition.model_validate(payload)])
    field(payload, "language")["presentation"]["conditions"] = [
        {"field": "title", "operator": "present", "effect": "required"}
    ]
    with pytest.raises(ProfileError, match="Ciclo"):
        registry([ProfileDefinition.model_validate(payload)])
    for condition in [
        {"field": "language", "operator": "equals", "effect": "visible"},
        {
            "field": "language",
            "operator": "present",
            "value": {"kind": "iri", "iri": "urn:test:x"},
            "effect": "visible",
        },
        {"field": "language", "operator": "execute", "effect": "visible"},
    ]:
        field(payload)["presentation"]["conditions"] = [condition]
        with pytest.raises(ValidationError):
            ProfileDefinition.model_validate(payload)


def test_invalid_condition_operand_type() -> None:
    payload = data()
    field(payload)["presentation"]["conditions"] = [
        {
            "field": "language",
            "operator": "equals",
            "value": {"kind": "literal", "value": "pt"},
            "effect": "visible",
        }
    ]
    with pytest.raises(ValueError, match="IRI"):
        registry([ProfileDefinition.model_validate(payload)])


def test_composition_rejects_missing_base_cycles_duplicate_releases_and_shape_conflicts() -> (
    None
):
    entries = definitions()
    child = altered(data("work-2.0.0.json"), base_release="urn:missing:release")
    with pytest.raises(ProfileError) as error:
        registry([*entries[:1], child])
    assert error.value.code == "unknown_release"
    base = altered(data(), base_release=entries[1].release_id)
    with pytest.raises(ProfileError) as error:
        registry([base, entries[1]])
    assert error.value.code == "composition_cycle"
    for repeated in [entries[0], altered(data(), release_id="urn:different:release")]:
        with pytest.raises(ProfileError) as error:
            registry([entries[0], repeated])
        assert error.value.code == "duplicate_release"
    child = altered(
        data("work-2.0.0.json"), validation=[{"id": SHAPES, "release": "9.0"}]
    )
    with pytest.raises(ProfileError) as error:
        registry([entries[0], child])
    assert error.value.code == "shape_conflict"


@pytest.mark.parametrize(
    "case,code",
    [
        ("implicit", "field_conflict"),
        ("semantic", "semantic_override"),
        ("renamed", "property_conflict"),
        ("missing", "missing_override"),
        ("relaxed", "policy_relaxation"),
        ("locked", "policy_override"),
    ],
)
def test_conflicting_overrides(case: str, code: str) -> None:
    core = data()
    child = override()
    changed = child["resources"][0]["fields"][0]
    if case == "implicit":
        changed["overrides"] = False
    elif case == "semantic":
        changed["property"] = BF + "note"
    elif case == "renamed":
        changed.update(name="anotherTitle", overrides=False)
    elif case == "missing":
        changed["name"] = "missingTitle"
    elif case == "relaxed":
        changed["policy"].update(min_count=0, required=False)
    elif case == "locked":
        field(core)["policy"]["specializable"] = False
        changed["policy"].update(min_count=2, specializable=False)
    with pytest.raises(ProfileError) as error:
        registry(
            [
                ProfileDefinition.model_validate(core),
                ProfileDefinition.model_validate(child),
            ]
        )
    assert error.value.code == code


def test_authorized_specialization_and_reorganization() -> None:
    child = override()
    changed = child["resources"][0]["fields"][0]
    changed["policy"]["min_count"] = 2
    changed["presentation"]["order"] = 99
    resolved = registry(
        [definitions()[0], ProfileDefinition.model_validate(child)]
    ).resolve(child["release_id"])
    work = next(r for r in resolved.resources if r.entity == EntityType.WORK)
    assert work.fields[-1].name == "title"
    assert work.fields[-1].policy.min_count == 2
    assert work.sections[0].id == "description"
    assert load_profile_registry().resolve(CORE) != resolved


@pytest.mark.parametrize(
    "kind,datatype,value,input",
    [
        ("number", "integer", "42", "number"),
        ("number", "decimal", "1.5", "number"),
        ("number", "double", "1.2e3", "number"),
        ("boolean", "boolean", "true", "boolean"),
        ("date", "date", "2026-10-12", "date"),
        ("date", "gYear", "2026", "date"),
        ("date", "dateTime", "2026-10-12T09:15:00Z", "date"),
    ],
)
def test_typed_defaults_and_roundtrip(
    kind: str, datatype: str, value: str, input: str
) -> None:
    field_data = {
        "name": kind,
        "property": "urn:test:value",
        "namespace": "test",
        "label": {"pt": kind},
        "value": {"kind": "typed", "datatypes": [XSD + datatype]},
        "policy": {
            "default": {"kind": "literal", "value": value, "datatype": XSD + datatype}
        },
        "presentation": {"input": input},
    }
    contract = DeclarativeField.model_validate(field_data)
    assert DeclarativeField.model_validate_json(contract.model_dump_json()) == contract
    field_data["policy"]["default"]["value"] = "invalid"
    with pytest.raises(ValidationError):
        DeclarativeField.model_validate(field_data)


@pytest.mark.parametrize(
    "default",
    [
        {"kind": "literal", "value": "2026-02-30", "datatype": XSD + "date"},
        {
            "kind": "literal",
            "value": "2026-01-01T25:00:00",
            "datatype": XSD + "dateTime",
        },
        {"kind": "literal", "value": "0000", "datatype": XSD + "gYear"},
    ],
)
def test_bad_calendar_defaults(default: dict) -> None:
    with pytest.raises(ValidationError):
        DeclarativeField.model_validate(
            {
                "name": "date",
                "property": "urn:test:date",
                "namespace": "test",
                "label": {"pt": "Data"},
                "value": {"kind": "typed", "datatypes": [default["datatype"]]},
                "policy": {"default": default},
                "presentation": {"input": "date"},
            }
        )


def test_language_defaults_enums_and_structured_values_are_not_strings() -> None:
    payload = field(data(), "mainTitle", BF + "Title")
    payload["value"]["language"].update(required=True, allowed=["pt", "en"])
    payload["policy"]["default"] = {
        "kind": "literal",
        "value": "Título",
        "language": "pt",
    }
    DeclarativeField.model_validate(payload)
    for default in [
        {"kind": "literal", "value": "Título", "language": "fr"},
        {"kind": "literal", "value": "Título"},
        {"kind": "iri", "iri": "urn:test:title"},
    ]:
        payload["policy"]["default"] = default
        with pytest.raises(ValidationError):
            DeclarativeField.model_validate(payload)
    payload = field(data())
    payload["policy"]["default"] = {"kind": "literal", "value": "Auxiliar achatado"}
    with pytest.raises(ValidationError):
        DeclarativeField.model_validate(payload)
    digital = data("digital-2.0.0.json")["resources"][0]["fields"][0]
    digital["policy"]["default"] = {"kind": "literal", "value": "not-in-enum"}
    with pytest.raises(ValidationError):
        DeclarativeField.model_validate(digital)


def test_external_namespace_registry_and_deprecated_properties() -> None:
    semantics = json.loads(V2.joinpath("semantics-v1.json").read_text())
    shapes = ShapeRegistry.model_validate_json(
        V2.joinpath("shapes-v1.json").read_text()
    )
    assert {n["prefix"] for n in semantics["namespaces"]} >= {
        "bf",
        "rdf",
        "rdfs",
        "skos",
        "dcterms",
        "schema",
        "prov",
    }
    for status in ["deprecated", "review_required"]:
        changed = copy.deepcopy(semantics)
        next(p for p in changed["properties"] if p["iri"] == BF + "title")["status"] = (
            status
        )
        with pytest.raises(ProfileError) as error:
            ProfileRegistry(
                [definitions()[0]], SemanticRegistry.model_validate(changed), shapes
            )
        assert error.value.code == "unsupported_property"
    extended = copy.deepcopy(semantics)
    extended["namespaces"].append(
        {
            "prefix": "local",
            "iri": "urn:example:ontology:",
            "ontology": "urn:example:ontology:",
            "release": "1.0",
        }
    )
    extended["properties"].append(
        {
            "iri": "urn:example:ontology:title",
            "sources": [BF + "Work"],
            "value_kinds": ["text"],
            "label": {"pt": "Título local"},
        }
    )
    payload = data()
    resource(payload)["fields"].append(
        {
            "name": "local_title",
            "property": "urn:example:ontology:title",
            "namespace": "local",
            "label": {"pt": "Título local"},
            "value": {"kind": "text"},
        }
    )
    ProfileRegistry(
        [ProfileDefinition.model_validate(payload)],
        SemanticRegistry.model_validate(extended),
        shapes,
    )
    assert field(payload)["property"] != resource(payload)["fields"][-1]["property"]


def test_registry_rejects_namespace_overlap_and_duplicate_shapes() -> None:
    payload = json.loads(V2.joinpath("semantics-v1.json").read_text())
    payload["namespaces"].append(
        {
            "prefix": "parent",
            "iri": "http://id.loc.gov/",
            "ontology": "http://id.loc.gov/",
            "release": "1.0",
        }
    )
    with pytest.raises(ValidationError):
        SemanticRegistry.model_validate(payload)
    payload = json.loads(V2.joinpath("shapes-v1.json").read_text())
    payload["sets"].append(payload["sets"][0])
    with pytest.raises(ValidationError):
        ShapeRegistry.model_validate(payload)


def test_selection_precedence_is_deterministic_and_explicit_release_never_replaced() -> (
    None
):
    a = registry()
    b = registry(list(reversed(definitions())))
    kwargs = {
        "statuses": ("candidate",),
        "resource_type": "urn:libris:resource-type:monograph",
        "institution": INSTITUTION,
    }
    assert a.candidates(EntityType.WORK, **kwargs) == b.candidates(
        EntityType.WORK, **kwargs
    )
    assert (
        a.select(EntityType.WORK, **kwargs).release_id
        == "urn:libris:profile:monograph:2.0.0"
    )
    assert a.select(EntityType.WORK, release_id=CORE, **kwargs).release_id == CORE
    for release in ["urn:missing:release", "urn:libris:profile:item:2.0.0"]:
        with pytest.raises(ProfileError):
            a.select(EntityType.WORK, release_id=release, **kwargs)
    with pytest.raises(ProfileError):
        a.select(EntityType.WORK)
    assert not a.candidates(EntityType.WORK, purpose="unknown", statuses=("candidate",))
    assert not a.candidates(EntityType.WORK, statuses=("deprecated",))


def test_published_used_and_composed_releases_immutable_and_copy_isolated() -> None:
    reg = load_profile_registry()
    original = reg.resolve(CORE)
    original.label["pt"] = "Alteração externa"
    assert reg.resolve(CORE).label["pt"] != original.label["pt"]
    incoming = reg.definition(CORE)
    incoming.label["pt"] = "Mutação externa"
    assert reg.definition(CORE).label["pt"] != incoming.label["pt"]
    with pytest.raises(ProfileError) as error:
        reg.replace_draft(incoming)
    assert error.value.code == "immutable_release"
    draft = altered(data(), status="draft")
    changed = altered(draft.model_dump(), label={"pt": "Nova descrição"})
    reg = registry([draft], used=(CORE,))
    with pytest.raises(ProfileError, match="imutável"):
        reg.replace_draft(changed)
    assert registry([draft]).replace_draft(changed).definition(CORE) == changed
    with pytest.raises(ProfileError, match="composição"):
        registry([draft, definitions()[1]]).replace_draft(changed)
    published = altered(data(), status="published")
    with pytest.raises(ProfileError, match="imutável"):
        registry([published]).replace_draft(
            altered(published.model_dump(), label={"pt": "Mudança"})
        )
    new_release = altered(
        data(), release="2.1.0", release_id="urn:libris:profile:core:2.1.0"
    )
    added = reg.add_release(new_release)
    assert added.resolve(CORE).release == "2.0.0"
    assert added.resolve(new_release.release_id).release == "2.1.0"


@pytest.mark.parametrize(
    "change,compatible",
    [
        ("label", True),
        ("section", True),
        ("optional", True),
        ("required", False),
        ("remove", False),
        ("uri", False),
        ("datatype", False),
        ("cardinality", False),
        ("vocabulary", False),
        ("shapes", False),
        ("deprecated", False),
    ],
)
def test_evolution_classification(change: str, compatible: bool) -> None:
    old = load_profile_registry().resolve(CORE)
    payload = old.model_dump(mode="json")
    if change == "label":
        field(payload)["label"]["pt"] = "Outro rótulo"
    elif change == "section":
        resource(payload)["sections"][0]["label"]["pt"] = "Outra seção"
    elif change in {"optional", "required"}:
        added = copy.deepcopy(field(payload, "language"))
        added.update(name="other_language", property="urn:extension:language")
        if change == "required":
            added["policy"].update(min_count=1, required=True)
        resource(payload)["fields"].append(added)
    elif change == "remove":
        resource(payload)["fields"] = [
            f for f in resource(payload)["fields"] if f["name"] != "language"
        ]
    elif change == "uri":
        field(payload)["property"] = "urn:extension:title"
    elif change == "datatype":
        field(payload, "date", BF + "Publication")["value"]["datatypes"] = [
            XSD + "date"
        ]
    elif change == "cardinality":
        field(payload)["policy"]["min_count"] = 2
    elif change == "vocabulary":
        field(payload, "language")["value"]["vocabulary"] = "urn:other:languages"
    elif change == "shapes":
        payload["validation"][0]["release"] = "2.0"
    elif change == "deprecated":
        payload["status"] = "deprecated"
    changes = compare_profiles(old, ResolvedProfile.model_validate(payload))
    assert changes and all(c.compatible == compatible for c in changes)


def test_legacy_models_ids_files_and_schema_preserved() -> None:
    hashes = json.loads(
        (ROOT / "tests/fixtures/bibliographic/profiles/legacy-sha256.json").read_text()
    )
    for name, digest in hashes.items():
        assert (
            hashlib.sha256(RESOURCES.joinpath(name).read_bytes()).hexdigest() == digest
        )
    assert ProfileField.model_fields["required_in_form"].alias == "requiredInForm"
    assert ProfileResource.model_fields["rdf_class"].alias == "class"
    for release_id in LEGACY_PROFILES:
        assert load_legacy_profile(release_id).id == release_id
    assert json.loads(
        RESOURCES.joinpath("catalog-profile.schema.json").read_text()
    ) == CatalogProfile.model_json_schema(by_alias=True)
    payload = load_legacy_profile("urn:libris:profile:monograph:v1:1").model_dump(
        by_alias=True
    )
    payload["resources"][0]["fields"][0]["requiredInForm"] = False
    with pytest.raises(ValidationError):
        CatalogProfile.model_validate(payload)


def test_generated_schema_artifacts_examples_and_discriminators() -> None:
    assert (
        json.loads(V2.joinpath("profile-2.0.schema.json").read_text())
        == ProfileDefinition.model_json_schema()
    )
    assert (
        json.loads(V2.joinpath("resolved-profile-2.0.schema.json").read_text())
        == ResolvedProfile.model_json_schema()
    )
    schema = ProfileDefinition.model_json_schema()
    assert (
        schema["$defs"]["DeclarativeField"]["properties"]["value"]["discriminator"][
            "propertyName"
        ]
        == "kind"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate_profile_artifacts.py"),
            "--check",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_openapi_contracts_in_isolated_fastapi_without_registering_production_routes() -> (
    None
):
    app = FastAPI()

    @app.post("/contract", response_model=ResolvedProfile)
    def interpret(payload: ProfileDefinition) -> ResolvedProfile:
        return registry([payload]).resolve(payload.release_id)

    schemas = app.openapi()["components"]["schemas"]
    assert "ProfileDefinition" in schemas
    assert "ResolvedProfile" in schemas
    assert schemas["DeclarativeField"]["additionalProperties"] is False
    assert "/contract" in app.openapi()["paths"]


def test_versioned_shapes_context_and_result_path_mapping() -> None:
    profile = load_profile_registry().resolve(CORE)
    graph = parse_document(
        (
            ROOT
            / "tests/fixtures/bibliographic/institutional/invalid-literal-work-reference.ttl"
        ).read_text(),
        "turtle",
    )
    context = ProfileValidationContext(profile_release=CORE)
    report = validate_resolved_profile(graph, profile, context)
    assert not report.conforms
    mapped = correlate_validation_issues(report, profile, graph)
    assert any(f.endswith("/instanceOf") for result in mapped for f in result.field_ids)
    legacy = validate_profile(graph, "urn:libris:profile:monograph:v1:1")
    assert legacy.conforms == report.conforms
    unknown = ValidationIssue(
        "urn:test:focus",
        "urn:unknown:property",
        str(SH.Violation),
        ("Erro fora do formulário",),
    )
    unmapped = correlate_validation_issues(
        ValidationReport(False, (unknown,), Graph()), profile, graph
    )
    assert unmapped[0].field_ids == () and unmapped[0].issue == unknown
    with pytest.raises(ProfileError):
        validate_resolved_profile(
            graph,
            profile,
            ProfileValidationContext(profile_release="urn:wrong:release"),
        )
    payload = profile.model_dump()
    payload["validation"][0]["id"] = "file:///etc/passwd"
    with pytest.raises(ProfileError):
        validate_resolved_profile(
            graph, ResolvedProfile.model_validate(payload), context
        )
    for name in (n for names in SHAPE_RELEASE_FILES.values() for n in names):
        assert "/" not in name and RESOURCES.joinpath(name).is_file()


def test_snapshot_context_preserves_uris_revisions_and_release() -> None:
    snapshot = SnapshotReference(
        uri="https://historical.example/resources/1", revision=3, profile_release=CORE
    )
    context = ProfileValidationContext(profile_release=CORE, snapshots=(snapshot,))
    assert (
        ProfileValidationContext.model_validate_json(context.model_dump_json())
        == context
    )
    assert context.snapshots[0].uri == snapshot.uri
    with pytest.raises(ValidationError):
        ProfileValidationContext(
            profile_release=CORE, mode="composed", snapshots=(snapshot, snapshot)
        )


def test_explicit_lifecycle_preserves_content_and_never_follows_replacement() -> None:
    reg = load_profile_registry()
    published = reg.transition_release(CORE, "published")
    assert reg.definition(CORE).status == "candidate"
    assert published.select(EntityType.WORK, release_id=CORE).release_id == CORE
    successor = altered(
        data(),
        release="2.1.0",
        release_id="urn:libris:profile:core:2.1.0",
        status="published",
    )
    deprecated = published.add_release(successor).transition_release(
        CORE, "deprecated", replacement=successor.release_id
    )
    assert deprecated.resolve(CORE).release_id == CORE
    assert deprecated.resolve(CORE).resources == published.resolve(CORE).resources
    assert deprecated.resolve(CORE).validation == published.resolve(CORE).validation
    with pytest.raises(ProfileError):
        deprecated.select(EntityType.WORK, release_id=CORE)
    assert deprecated.select(EntityType.WORK).release_id == successor.release_id
    with pytest.raises(ProfileError):
        deprecated.transition_release(CORE, "published")
    with pytest.raises(ProfileError):
        published.transition_release(
            CORE, "deprecated", replacement="urn:unknown:release"
        )


def test_composed_validation_reuses_engine_and_auxiliary_mapping() -> None:
    from libris.modules.bibliographic.infrastructure.ownership import (
        ENTITY_TYPES,
        compose_snapshots,
        owned_nodes,
    )
    from libris.modules.bibliographic.infrastructure.rdf import new_graph
    from rdflib import URIRef
    from rdflib.namespace import RDF

    def fixture(name: str) -> Graph:
        return parse_document(
            (
                ROOT / f"tests/fixtures/bibliographic/institutional/{name}.ttl"
            ).read_text(),
            "turtle",
        )

    def snapshots(graph: Graph) -> list[tuple[URIRef, Graph]]:
        parts: list[tuple[URIRef, Graph]] = []
        roots = {
            node for kind in ENTITY_TYPES for node in graph.subjects(RDF.type, kind)
        }
        for root in roots:
            assert isinstance(root, URIRef)
            owned = owned_nodes(graph, root)
            part = new_graph()
            for triple in graph:
                if triple[0] in owned:
                    part.add(triple)
            parts.append((root, part))
        return parts

    profile = load_profile_registry().resolve(CORE)
    composed = compose_snapshots(snapshots(fixture("multiple-items")))
    context = ProfileValidationContext(profile_release=CORE, mode="composed")
    assert validate_resolved_profile(composed, profile, context).conforms
    graph = fixture("invalid-numeric-title")
    report = validate_resolved_profile(
        graph, profile, ProfileValidationContext(profile_release=CORE)
    )
    assert not report.conforms
    mapped = correlate_validation_issues(report, profile, graph)
    assert any(f.endswith("/mainTitle") for issue in mapped for f in issue.field_ids)


@pytest.mark.parametrize(
    "case",
    json.loads(
        (ROOT / "tests/fixtures/bibliographic/profiles/manifest.json").read_text()
    ),
)
def test_invalid_fixture_corpus(case: dict) -> None:
    document = (
        ROOT / "tests/fixtures/bibliographic/profiles" / case["file"]
    ).read_text()
    if case["layer"] == "structural":
        with pytest.raises(ValidationError):
            ProfileDefinition.model_validate_json(document)
    else:
        with pytest.raises(ProfileError) as error:
            registry([ProfileDefinition.model_validate_json(document)])
        assert error.value.code == case["code"]


def test_shared_predicate_cannot_mix_source_specific_targets() -> None:
    payload = data()
    field(payload, "relatedTo")["value"]["target_entities"] = ["Instance"]
    field(payload, "relatedTo")["value"]["target_classes"] = [BF + "Instance"]
    with pytest.raises(ProfileError) as error:
        registry([ProfileDefinition.model_validate(payload)])
    assert error.value.code == "incompatible_target"


def test_bibframe_terms_are_from_reviewed_official_v2_inventory() -> None:
    inventory = json.loads(
        (
            ROOT / "tests/fixtures/bibliographic/profiles/bibframe-2.0.1-terms.json"
        ).read_text()
    )
    semantics = json.loads(V2.joinpath("semantics-v1.json").read_text())
    terms = {
        entry["iri"]
        for key in ("properties", "classes")
        for entry in semantics[key]
        if entry["iri"].startswith(BF)
    }
    assert terms <= set(inventory["terms"])
    assert inventory["version"] == "2.0.1"


def test_authority_and_relationship_configuration_uses_iris_not_embedded_entities() -> (
    None
):
    profile = load_profile_registry().resolve("urn:libris:profile:work-minimal:2.0.0")
    work = next(r for r in profile.resources if r.entity == EntityType.WORK)
    values = {field.value.kind: field.value for field in work.fields}
    assert values["authority"].node_kind == "IRI"
    assert values["relationship"].node_kind == "IRI"
    assert values["authority"].authority == "urn:example:authority:subjects"


def test_structured_form_cycles_and_missing_shape_coverage_are_rejected() -> None:
    payload = data()
    shape_payload = json.loads(V2.joinpath("shapes-v1.json").read_text())
    shape_payload["sets"][0]["classes"].remove(BF + "Work")
    semantics = SemanticRegistry.model_validate_json(
        V2.joinpath("semantics-v1.json").read_text()
    )
    with pytest.raises(ProfileError) as error:
        ProfileRegistry(
            [definitions()[0]], semantics, ShapeRegistry.model_validate(shape_payload)
        )
    assert error.value.code == "incompatible_shapes"
    f = field(payload, "mainTitle", BF + "Title")
    f["value"] = {"kind": "structured", "target_classes": [BF + "Title"]}
    f["presentation"]["input"] = "structured"
    with pytest.raises(ProfileError) as error:
        registry([ProfileDefinition.model_validate(payload)])
    assert error.value.code == "structure_cycle"


def test_shape_file_resolver_is_not_a_network_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    def deny_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Acesso à rede durante configuração/validação")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    profile = load_profile_registry().resolve(CORE)
    graph = parse_document(
        (
            ROOT / "tests/fixtures/bibliographic/institutional/single-author.ttl"
        ).read_text(),
        "turtle",
    )
    before = set(graph)
    assert validate_resolved_profile(
        graph, profile, ProfileValidationContext(profile_release=CORE)
    ).conforms
    assert set(graph) == before


def test_package_release_fingerprints_reject_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from libris.modules.bibliographic.domain.profile_registry import profile_fingerprint
    from libris.modules.bibliographic.infrastructure import profiles as loader

    locks = json.loads(V2.joinpath("release-lock.json").read_text())
    for definition in definitions():
        if definition.status != "draft":
            assert locks[definition.release_id] == profile_fingerprint(definition)
    declaration = definitions()[0]
    transitioned = altered(declaration.model_dump(), status="published")
    assert profile_fingerprint(declaration) == profile_fingerprint(transitioned)
    read = loader._read

    def changed(name: str) -> str:
        if name == "profiles-v2/core-2.0.0.json":
            payload = data()
            payload["label"]["pt"] = "Release alterada indevidamente"
            return json.dumps(payload)
        return read(name)

    monkeypatch.setattr(loader, "_read", changed)
    with pytest.raises(ProfileError) as error:
        loader.load_profile_registry()
    assert error.value.code == "immutable_release"


def test_artifact_generator_never_overwrites_existing_release_fingerprints(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "generate_profile_artifacts.py"
    script.write_text((ROOT / "scripts/generate_profile_artifacts.py").read_text())
    target = (
        tmp_path / "apps/api/src/libris/modules/bibliographic/resources/profiles-v2"
    )
    shutil.copytree(Path(str(V2)), target)
    before = (target / "release-lock.json").read_text()
    changed = data()
    changed["label"]["pt"] = "Alteração de release fixada"
    (target / "core-2.0.0.json").write_text(json.dumps(changed))
    result = subprocess.run(
        [sys.executable, str(script), "--lock-new-releases"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "Release imutável" in result.stderr
    assert (target / "release-lock.json").read_text() == before
