"""Conformance without a database: the resolver below is explicitly a test double."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from libris.modules.bibliographic.api import schemas
from libris.modules.bibliographic.api.schemas import relationships as contracts
from libris.modules.bibliographic.api.schemas.common import (
    EntityReference,
    LocalReference,
    ResourceReference,
)
from libris.modules.bibliographic.api.schemas.work import WorkCreate
from libris.modules.bibliographic.application.relationships import (
    ResolvedEntity,
    validate_cycles,
    validate_references,
)
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.relationships import (
    BF,
    DEFAULT_REGISTRY,
    INITIAL_RELATIONS,
    SKOS,
    EntityType,
    RelationDefinition,
)
from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).parents[1] / "fixtures/bibliographic/relationships"
EXAMPLES = json.loads((ROOT / "manifest.json").read_text())["examples"]


def reference(number: int, entity_type: str = "Work") -> dict[str, str]:
    identifier = f"00000000-0000-4000-8000-{number:012}"
    return dict(
        kind="local",
        entity_type=entity_type,
        internal_id=identifier,
        uri=f"https://catalog.example.invalid/resources/{identifier}",
    )


def link(
    target: dict[str, str],
    predicate: str = BF + "subject",
    source: dict[str, str] | None = None,
) -> dict[str, object]:
    return dict(kind="direct", source=source or reference(1), target=target, predicate=predicate)


class MemoryResolver:
    """Synthetic identities, never evidence of a PostgreSQL existence check."""

    def __init__(self) -> None:
        self.entities = [
            ResolvedEntity(
                ResourceIdentity(UUID(reference(n)["internal_id"]), reference(n)["uri"]),
                EntityType(kind),
                "Organization" if kind == "Agent" else None,
            )
            for n, kind in [
                (1, "Work"),
                (2, "Instance"),
                (3, "Item"),
                (4, "Agent"),
                (5, "Subject"),
                (6, "Work"),
                (7, "Instance"),
                (8, "Subject"),
            ]
        ]

    async def resolve(self, ref: LocalReference) -> ResolvedEntity | None:
        return next(
            (
                entity
                for entity in self.entities
                if entity.identity.internal_id == ref.internal_id or entity.identity.uri == ref.uri
            ),
            None,
        )


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e["file"])
def test_versioned_examples(example: dict[str, object]) -> None:
    data = json.loads((ROOT / str(example["file"])).read_text())
    schema = (
        TypeAdapter(EntityReference)
        if example["schema"] == "EntityReference"
        else TypeAdapter(getattr(schemas, str(example["schema"])))
    )

    def validate() -> None:
        value = schema.validate_python(data)
        # JSON round-trip including PATCH presence/null semantics.
        serialized = value.model_dump_json(exclude_unset=True)
        assert schema.validate_json(serialized) == value
        if example["stage"] == "update":
            state = contracts.RelationshipCreate.model_validate_json(
                (ROOT / str(example["current"])).read_text()
            )
            result = contracts.apply_relationship_update(state, value)
            assert len(state.links) == 2
            if "count" in example:
                assert len(result.links) == example["count"]
        elif example["stage"] == "integrity":
            asyncio.run(validate_references(value, MemoryResolver()))

    if str(example["file"]).startswith("invalid/"):
        with pytest.raises((ValidationError, ValueError)):
            validate()
    else:
        validate()


@pytest.mark.parametrize(
    "payload",
    [
        reference(1),
        {"kind": "external", "entity_type": "Work", "uri": "urn:test:work"},
        {
            "kind": "unreconciled",
            "entity_type": "Work",
            "namespace": "urn:test:",
            "key": "row1",
            "label": "Transcrição",
        },
    ],
)
def test_reference_roundtrip(payload: dict[str, str]) -> None:
    adapter = TypeAdapter(EntityReference)
    value = adapter.validate_python(payload)
    assert adapter.validate_json(adapter.dump_json(value)) == value


@pytest.mark.parametrize(
    "change",
    [
        {"entity_type": "Invalid"},
        {"uri": "relative"},
        {"uri": "https://"},
        {"uri": "https://example.invalid/%xx"},
        {"uri": "https://example.invalid/x y"},
        {"internal_id": "not-a-uuid"},
        {"namespace": "urn:test:"},
        {"label": "Name"},
    ],
)
def test_invalid_local_reference(change: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(EntityReference).validate_python(reference(1) | change)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "external",
            "entity_type": "Agent",
            "uri": "urn:test:x",
            "external_id": "4",
        },
        {
            "kind": "unreconciled",
            "entity_type": "Agent",
            "namespace": "urn:test:",
            "key": "x",
        },
        {
            "kind": "unreconciled",
            "entity_type": "Agent",
            "namespace": "urn:test:",
            "key": "x",
            "label": "  ",
        },
    ],
)
def test_required_reference_attributes(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(EntityReference).validate_python(payload)


@pytest.mark.parametrize("definition", INITIAL_RELATIONS, ids=lambda d: d.name)
def test_registered_relationships(definition: RelationDefinition) -> None:
    source = reference(1, definition.source)
    target = reference(5, next(iter(definition.targets)))
    payload = link(target, definition.predicate, source)
    if definition.role_required:
        payload.update(kind="contribution", role=["http://id.loc.gov/vocabulary/relators/aut"])
    elif definition.auxiliary_class:
        payload.update(kind="publication", activity_key="publication-1")
    parsed = TypeAdapter(contracts.Link).validate_python(payload)
    assert parsed.predicate == definition.predicate
    assert DEFAULT_REGISTRY.get(parsed.source.entity_type, parsed.predicate) == definition


@pytest.mark.parametrize("definition", INITIAL_RELATIONS, ids=lambda d: d.name)
def test_incompatible_targets(definition: RelationDefinition) -> None:
    wrong = next(kind for kind in EntityType if kind not in definition.targets)
    with pytest.raises(ValidationError):
        TypeAdapter(contracts.Link).validate_python(
            link(
                reference(5, wrong),
                definition.predicate,
                reference(1, definition.source),
            )
        )


def test_extension_is_explicit_and_does_not_redefine_canonical_semantics() -> None:
    definition = RelationDefinition(
        "test_relation",
        "Relação de teste",
        EntityType.WORK,
        frozenset([EntityType.SUBJECT]),
        "https://vocab.example.invalid/relation",
        "test_relation",
    )
    registry = DEFAULT_REGISTRY.extend(definition, "https://vocab.example.invalid/")
    payload = dict(source=reference(1), links=[link(reference(5, "Subject"), definition.predicate)])
    with pytest.raises(ValidationError):
        contracts.RelationshipCreate.model_validate(payload)
    result = contracts.RelationshipCreate.model_validate(payload, context={"registry": registry})
    assert result.links[0].predicate == definition.predicate
    for predicate in [BF + "invented", BF + "subject", SKOS + "invented"]:
        with pytest.raises(ValueError):
            DEFAULT_REGISTRY.extend(
                replace(definition, predicate=predicate),
                "http://id.loc.gov/ontologies/",
            )
    with pytest.raises(ValueError):
        registry.extend(definition, "https://vocab.example.invalid/")


def test_profile_minimum_is_not_an_ontology_axiom() -> None:
    state = contracts.RelationshipCreate.model_validate(
        {"source": reference(2, "Instance")}, context={"minimums": {}}
    )
    assert state.links == []
    with pytest.raises(ValidationError):
        contracts.RelationshipCreate.model_validate(state.model_dump())


def test_collection_operations_are_atomic_and_distinguish_omission() -> None:
    current = contracts.RelationshipCreate.model_validate_json(
        (ROOT / "valid/work-create.json").read_text()
    )
    absent = contracts.RelationshipUpdate.model_validate({})
    empty = contracts.RelationshipUpdate.model_validate({"operations": []})
    assert absent.model_fields_set == set()
    assert empty.model_fields_set == {"operations"}
    assert absent.model_dump(exclude_unset=True) == {}
    assert empty.model_dump(exclude_unset=True) == {"operations": []}
    invalid = contracts.RelationshipUpdate.model_validate(
        {
            "operations": [
                {"op": "remove", "links": [current.links[0].model_dump()]},
                {"op": "add", "links": [current.links[1].model_dump()]},
            ]
        }
    )
    original = current.model_dump_json()
    with pytest.raises(ValidationError):
        contracts.apply_relationship_update(current, invalid)
    assert current.model_dump_json() == original


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "unknown", "links": []},
        {"op": "add", "links": []},
        {"op": "remove", "links": []},
        {
            "op": "replace",
            "predicate": BF + "subject",
            "links": [link(reference(6), BF + "relatedTo")],
        },
    ],
)
def test_invalid_operations(operation: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        contracts.RelationshipUpdate.model_validate({"operations": [operation]})


def test_remove_mandatory_link_and_unknown_replace_rejected() -> None:
    current = contracts.RelationshipCreate.model_validate_json(
        (ROOT / "valid/instance-create.json").read_text()
    )
    for operation in [
        {"op": "remove", "links": [current.links[0].model_dump()]},
        {"op": "replace", "predicate": "urn:unknown:predicate", "links": []},
        {"op": "replace", "predicate": BF + "hasItem", "links": []},
    ]:
        update = contracts.RelationshipUpdate.model_validate({"operations": [operation]})
        with pytest.raises(ValueError):
            contracts.apply_relationship_update(current, update)


def test_referential_resolution_and_legitimate_shared_authorities() -> None:
    for number in [1, 6]:
        state = contracts.RelationshipCreate.model_validate(
            dict(
                source=reference(number),
                links=[
                    dict(
                        kind="contribution",
                        source=reference(number),
                        target=reference(4, "Agent"),
                        role=["http://id.loc.gov/vocabulary/relators/aut"],
                    )
                ],
            )
        )
        report = asyncio.run(validate_references(state, MemoryResolver()))
        assert report.pending == ()


def test_unreconciled_requires_draft_and_external_is_reported_without_network() -> None:
    state = contracts.RelationshipCreate.model_validate_json(
        (ROOT / "valid/unreconciled-agent.json").read_text()
    )
    with pytest.raises(ValueError, match="rascunho"):
        asyncio.run(validate_references(state, MemoryResolver()))
    report = asyncio.run(validate_references(state, MemoryResolver(), allow_unreconciled=True))
    assert len(report.pending) == 1
    external = contracts.RelationshipCreate.model_validate_json(
        (ROOT / "valid/external-subject.json").read_text()
    )
    assert len(asyncio.run(validate_references(external, MemoryResolver())).pending) == 1


def test_alias_duplicates_and_identity_mismatch_after_resolution() -> None:
    first = reference(5, "Subject")
    second = {key: value for key, value in first.items() if key != "uri"}
    first.pop("internal_id")
    state = contracts.RelationshipCreate.model_validate(
        dict(source=reference(1), links=[link(first), link(second)])
    )
    with pytest.raises(ValueError, match="duplicado"):
        asyncio.run(validate_references(state, MemoryResolver()))
    inconsistent = reference(5, "Subject") | {"uri": reference(8)["uri"]}
    state = contracts.RelationshipCreate.model_validate(
        dict(source=reference(1), links=[link(inconsistent)])
    )
    with pytest.raises(ValueError, match="identificam|armazenada"):
        asyncio.run(validate_references(state, MemoryResolver()))


def test_real_target_type_and_role_policy() -> None:
    wrong = reference(4, "Subject")
    state = contracts.RelationshipCreate.model_validate(
        dict(source=reference(1), links=[link(wrong)])
    )
    with pytest.raises(ValueError, match="Tipo real"):
        asyncio.run(validate_references(state, MemoryResolver()))
    state = contracts.RelationshipCreate.model_validate_json(
        (ROOT / "valid/work-agents.json").read_text()
    )
    with pytest.raises(ValueError, match="Papel"):
        asyncio.run(validate_references(state, MemoryResolver(), allowed_roles=frozenset()))
    assert asyncio.run(validate_references(state, MemoryResolver())).pending == ()


@pytest.mark.parametrize(
    "predicate,kind", [(BF + "translationOf", "Work"), (SKOS + "broader", "Subject")]
)
def test_cycles_require_complete_supplied_closure(predicate: str, kind: str) -> None:
    adapter = TypeAdapter(contracts.Link)
    edges = [
        adapter.validate_python(link(reference(b, kind), predicate, reference(a, kind)))
        for a, b in [(1, 2), (2, 3), (3, 1)]
    ]
    validate_cycles(edges[:2])
    with pytest.raises(ValueError, match="Ciclo"):
        validate_cycles(edges)
    related = [
        adapter.validate_python(link(reference(b), BF + "relatedTo", reference(a)))
        for a, b in [(1, 2), (2, 1)]
    ]
    validate_cycles(related)


def test_existing_dtos_accept_typed_and_legacy_references_without_recursion() -> None:
    title = [{"main_title": [{"value": "Obra sintética"}]}]
    data = WorkCreate.model_validate(dict(title=title, subject=[reference(5, "Subject")]))
    assert data.subject[0].entity_type == EntityType.SUBJECT
    legacy = WorkCreate.model_validate(dict(title=title, subject=[{"uri": "urn:test:subject"}]))
    assert isinstance(legacy.subject[0], ResourceReference)
    with pytest.raises(ValidationError):
        WorkCreate.model_validate(dict(title=title, subject=[reference(4, "Agent")]))
    with pytest.raises(ValidationError):
        WorkCreate.model_validate(
            dict(title=title, subject=[reference(5, "Subject") | {"title": title}])
        )


def test_json_schema_and_semantic_paths() -> None:
    schema = contracts.RelationshipUpdate.model_json_schema()
    assert schema["properties"]["operations"]["type"] == "array"
    assert "anyOf" not in schema["properties"]["operations"]
    schema = contracts.RelationshipCreate.model_json_schema()
    assert schema["properties"]["links"]["items"]["discriminator"]["propertyName"] == "kind"
    contribution = DEFAULT_REGISTRY.get(EntityType.WORK, BF + "contribution")
    assert contribution.path == (BF + "contribution", BF + "agent")
    assert contribution.role_required and contribution.auxiliary_class == BF + "Contribution"
    for definition in INITIAL_RELATIONS:
        assert definition.predicate.startswith((BF, SKOS, "http://purl.org/dc/terms/"))
        if definition.path:
            assert definition.path[0] == definition.predicate
        if definition.inverse and definition.inverse.startswith(BF) and definition.derived:
            inverse = next(
                entry for entry in INITIAL_RELATIONS if entry.predicate == definition.inverse
            )
            assert inverse.inverse == definition.predicate


def test_application_update_resolves_local_aliases_and_preserves_stored_uri() -> None:
    from libris.modules.bibliographic.application.relationships import (
        apply_resolved_relationship_update,
    )

    id_only = {key: value for key, value in reference(5, "Subject").items() if key != "uri"}
    uri_only = {
        key: value for key, value in reference(5, "Subject").items() if key != "internal_id"
    }
    current = contracts.RelationshipCreate.model_validate(
        {"source": reference(1), "links": [link(id_only)]}
    )
    patch = contracts.RelationshipUpdate.model_validate(
        {"operations": [{"op": "remove", "links": [link(uri_only)]}]}
    )
    result, report = asyncio.run(
        apply_resolved_relationship_update(current, patch, MemoryResolver())
    )
    assert result.links == [] and report.pending == ()
    assert len(current.links) == 1
    assert result.source.uri == reference(1)["uri"]
    duplicate = contracts.RelationshipUpdate.model_validate(
        {"operations": [{"op": "add", "links": [link(uri_only)]}]}
    )
    with pytest.raises(ValidationError):
        asyncio.run(apply_resolved_relationship_update(current, duplicate, MemoryResolver()))


def test_operation_cannot_hide_wrong_origin_or_derived_link() -> None:
    current = contracts.RelationshipCreate.model_validate({"source": reference(1)})
    for invalid in [
        link(reference(5, "Subject"), source=reference(6)),
        link(reference(2, "Instance"), BF + "hasInstance"),
    ]:
        patch = contracts.RelationshipUpdate.model_validate(
            {
                "operations": [
                    {"op": "add", "links": [invalid]},
                    {"op": "remove", "links": [invalid]},
                ]
            }
        )
        with pytest.raises(ValueError):
            contracts.apply_relationship_update(current, patch)


def test_held_by_candidate_requires_organization() -> None:
    state = contracts.RelationshipCreate.model_validate(
        {
            "source": reference(3, "Item"),
            "links": [
                link(reference(2, "Instance"), BF + "itemOf", reference(3, "Item")),
                link(reference(4, "Agent"), BF + "heldBy", reference(3, "Item")),
            ],
        }
    )
    resolver = MemoryResolver()
    assert asyncio.run(validate_references(state, resolver)).pending == ()
    resolver.entities[3] = replace(resolver.entities[3], agent_kind="Person")
    with pytest.raises(ValueError, match="Organization"):
        asyncio.run(validate_references(state, resolver))


def test_publication_groups_remain_distinct_and_roles_are_preserved() -> None:
    state = contracts.RelationshipCreate.model_validate_json(
        (ROOT / "valid/publication.json").read_text()
    )
    other = state.links[1].model_dump() | {"activity_key": "publication-2"}
    combined = contracts.RelationshipCreate.model_validate(
        state.model_dump() | {"links": [link.model_dump() for link in state.links] + [other]}
    )
    assert asyncio.run(validate_references(combined, MemoryResolver())).pending == ()
    value = contracts.RelationshipCreate.model_validate_json(
        (ROOT / "valid/work-agents.json").read_text()
    )
    assert contracts.RelationshipCreate.model_validate_json(value.model_dump_json()) == value
    assert {role for entry in value.links for role in contracts.roles(entry)} == {
        "http://id.loc.gov/vocabulary/relators/aut",
        "http://id.loc.gov/vocabulary/relators/edt",
        "http://id.loc.gov/vocabulary/relators/trl",
    }
    invalid = value.links[0].model_dump()
    invalid["role"] *= 2
    with pytest.raises(ValidationError):
        TypeAdapter(contracts.Link).validate_python(invalid)


def test_metadata_inverses_match_verified_vocabulary() -> None:
    # Verified in lcnetdev/bibframe-ontology/bibframe.rdf, not inferred from field names.
    assert DEFAULT_REGISTRY.get(EntityType.WORK, BF + "translationOf").inverse == BF + "translation"
    for kind in [EntityType.WORK, EntityType.INSTANCE]:
        relation = DEFAULT_REGISTRY.get(kind, BF + "relatedTo")
        assert relation.symmetric and relation.inverse is None
    assert DEFAULT_REGISTRY.get(EntityType.WORK, BF + "subject").inverse is None


def test_matrix_covers_the_executable_registry() -> None:
    documentation = (Path(__file__).parents[2] / "docs/architecture/relationships.md").read_text()
    rows = [
        line.split("|")[1].strip() for line in documentation.splitlines() if line.startswith("| ")
    ]
    assert {entry.name for entry in INITIAL_RELATIONS}.issubset(rows)


def test_resolved_local_and_external_uri_do_not_duplicate() -> None:
    local = {key: value for key, value in reference(5, "Subject").items() if key != "uri"}
    external = {"kind": "external", "entity_type": "Subject", "uri": reference(5)["uri"]}
    state = contracts.RelationshipCreate.model_validate(
        {
            "source": reference(1),
            "links": [
                link(local),
                link(external),
            ],
        }
    )
    with pytest.raises(ValueError, match="duplicado"):
        asyncio.run(validate_references(state, MemoryResolver()))


def test_registered_profile_cardinality_minimum_and_maximum() -> None:
    definition = RelationDefinition(
        "bounded",
        "Relação de teste com limites explícitos",
        EntityType.WORK,
        frozenset([EntityType.SUBJECT]),
        "https://vocab.example.invalid/bounded",
        "bounded",
        minimum=1,
        maximum=2,
    )
    registry = DEFAULT_REGISTRY.extend(definition, "https://vocab.example.invalid/")
    targets = [reference(n, "Subject") for n in [5, 8, 9]]
    for count in [0, 3]:
        with pytest.raises(ValidationError, match="Cardinalidade"):
            contracts.RelationshipCreate.model_validate(
                {
                    "source": reference(1),
                    "links": [link(t, definition.predicate) for t in targets[:count]],
                },
                context={"registry": registry},
            )
    value = contracts.RelationshipCreate.model_validate(
        {"source": reference(1), "links": [link(t, definition.predicate) for t in targets[:2]]},
        context={"registry": registry},
    )
    assert len(value.links) == 2
    with pytest.raises(ValueError):
        DEFAULT_REGISTRY.extend(
            replace(definition, minimum=3, maximum=2), "https://vocab.example.invalid/"
        )


def test_self_alias_rejected_after_resolution() -> None:
    source = {key: value for key, value in reference(1).items() if key != "uri"}
    target = {key: value for key, value in reference(1).items() if key != "internal_id"}
    value = contracts.RelationshipCreate.model_validate(
        {"source": source, "links": [link(target, BF + "relatedTo", source)]}
    )
    with pytest.raises(ValueError, match="Autorreferência"):
        asyncio.run(validate_references(value, MemoryResolver()))
