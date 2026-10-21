"""Structural contracts only: no database, RDF converters or fictitious persistence."""

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from libris.modules.bibliographic.api import schemas
from libris.modules.bibliographic.api.schemas.common import (
    IRI,
    Classification,
    Contribution,
    Extent,
    Identifier,
    LanguageTag,
    LocalizedText,
    Note,
    Publication,
    PublicationDate,
    ResourceReference,
    ResourceURI,
    ShelfMark,
    Title,
)
from pydantic import BaseModel, TypeAdapter, ValidationError

FIXTURES = Path(__file__).parents[1] / "fixtures/bibliographic/contracts"
RESOURCES = ("Work", "Instance", "Item", "Agent", "Subject")
REQUIRED = {
    "Work": ("title",),
    "Instance": ("instance_of",),
    "Item": ("item_of", "identified_by"),
    "Agent": ("kind", "authorized_name"),
    "Subject": ("preferred_label", "scheme"),
}
OPTIONAL = {
    "Work": "subject",
    "Instance": "title",
    "Item": "held_by",
    "Agent": "variant_name",
    "Subject": "alternative_label",
}


def model(name: str, operation: str) -> type[BaseModel]:
    return getattr(schemas, name + operation)


def payload(name: str, operation: str) -> dict[str, object]:
    return json.loads(
        (FIXTURES / f"{name.lower()}-{operation.lower()}.json").read_text()
    )


@pytest.mark.parametrize("name", RESOURCES)
@pytest.mark.parametrize("operation", ("Create", "Read", "Update"))
def test_fixture_round_trip(name: str, operation: str) -> None:
    cls = model(name, operation)
    value = cls.model_validate(payload(name, operation))
    dumped = value.model_dump(mode="json", exclude_unset=operation == "Update")
    assert cls.model_validate_json(json.dumps(dumped)) == value
    assert cls.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("name", RESOURCES)
@pytest.mark.parametrize("operation", ("Create", "Read"))
def test_required_properties(name: str, operation: str) -> None:
    for field in REQUIRED[name]:
        data = payload(name, operation)
        del data[field]
        with pytest.raises(ValidationError):
            model(name, operation).model_validate(data)
        data[field] = None
        with pytest.raises(ValidationError):
            model(name, operation).model_validate(data)
        data[field] = []
        with pytest.raises(ValidationError):
            model(name, operation).model_validate(data)


@pytest.mark.parametrize("name", RESOURCES)
@pytest.mark.parametrize("operation", ("Create", "Read", "Update"))
def test_unknown_fields(name: str, operation: str) -> None:
    with pytest.raises(ValidationError):
        model(name, operation).model_validate(
            dict(payload(name, operation), metadata={})
        )


@pytest.mark.parametrize("name", RESOURCES)
@pytest.mark.parametrize(
    "field", ("internal_id", "uri", "kind", "revision", "actor_id")
)
def test_immutable_or_private_fields_in_update(name: str, field: str) -> None:
    with pytest.raises(ValidationError):
        model(name, "Update").model_validate({field: "unexpected"})


@pytest.mark.parametrize("name", RESOURCES)
def test_patch_presence_null_empty_and_replacement(name: str) -> None:
    cls = model(name, "Update")
    field = OPTIONAL[name]
    empty = cls.model_validate({})
    assert empty.model_fields_set == set()
    assert empty.model_dump(exclude_unset=True) == {}
    for value in (
        None,
        [],
        [{"uri": "urn:libris:test:subject"}]
        if name in {"Work", "Item"}
        else [{"main_title": [{"value": "Outro título"}]}]
        if name == "Instance"
        else [{"value": "Outra forma", "language": "pt"}],
    ):
        patched = cls.model_validate({field: value})
        assert patched.model_fields_set == {field}
        assert patched.model_dump(mode="json", exclude_unset=True) == {field: value}
    for field in REQUIRED[name]:
        if field == "kind":
            continue
        with pytest.raises(ValidationError):
            cls.model_validate({field: None})
        with pytest.raises(ValidationError):
            cls.model_validate({field: []})
        new_value = payload(name, "Create")[field]
        assert cls.model_validate({field: new_value}).model_fields_set == {field}
        prop = cls.model_json_schema()["properties"][field]
        assert "null" not in json.dumps(prop)


@pytest.mark.parametrize("name", RESOURCES)
def test_optional_create_fields_and_default_isolation(name: str) -> None:
    cls = model(name, "Create")
    data = {
        key: value
        for key, value in payload(name, "Create").items()
        if key in REQUIRED[name]
    }
    first, second = cls.model_validate(data), cls.model_validate(data)
    field = OPTIONAL[name]
    assert getattr(first, field) == []
    getattr(first, field).append("mutation")
    assert getattr(second, field) == []
    with pytest.raises(ValidationError):
        cls.model_validate(dict(data, **{field: None}))
    with pytest.raises(ValidationError):
        cls.model_validate(dict(data, **{field: "invalid"}))


@pytest.mark.parametrize(
    "value",
    [
        "urn:libris:test:subjects",
        "https://EXAMPLE.invalid/a%2Fb#part",
        "http://id.loc.gov/vocabulary/languages/por",
        "doi:10.1/test",
    ],
)
def test_iri_preserves_text(value: str) -> None:
    assert TypeAdapter(IRI).validate_python(value) == value
    assert (
        ResourceReference.model_validate_json(json.dumps({"uri": value})).uri == value
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "relative/path",
        "https:///no-host",
        "urn:",
        "urn:test:with space",
        "urn:test:\n",
        "urn:test:<x>",
        123,
    ],
)
def test_invalid_iri(value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(IRI).validate_python(value)


@pytest.mark.parametrize(
    "value",
    [
        "urn:libris:entity",
        "https://example.invalid/a?x=1",
        "https://example.invalid/a#x",
        "https://user:pw@example.invalid/a",
    ],
)
def test_invalid_persistent_uri(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ResourceURI).validate_python(value)


@pytest.mark.parametrize("name", RESOURCES)
@pytest.mark.parametrize(
    "field,value", [("internal_id", "not-a-uuid"), ("uri", "urn:local:1")]
)
def test_invalid_read_identity(name: str, field: str, value: str) -> None:
    data = payload(name, "Read")
    data[field] = value
    with pytest.raises(ValidationError):
        model(name, "Read").model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"internal_id": "00000000-0000-4000-8000-000000000001"},
        {"uri": "urn:test:1", "kind": "Work"},
        {"uri": "urn:test:1", "label": "embedded"},
    ],
)
def test_reference_does_not_invent_identifier_alternatives(
    data: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ResourceReference.model_validate(data)


@pytest.mark.parametrize(
    "tag", ["pt", "pt-BR", "zh-Hant", "en-US", "x-local", "i-klingon"]
)
def test_language_tags(tag: str) -> None:
    assert TypeAdapter(LanguageTag).validate_python(tag) == tag


@pytest.mark.parametrize("tag", ["", "pt_BR", "en-", "12", "en US", "en\n"])
def test_invalid_language_tags(tag: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(LanguageTag).validate_python(tag)


@pytest.mark.parametrize("value", ["", 123, None])
def test_invalid_text(value: object) -> None:
    with pytest.raises(ValidationError):
        LocalizedText.model_validate({"value": value})


def test_text_is_transcribed_without_normalization() -> None:
    data = {"value": "  Título transcrito  ", "language": "PT-br"}
    assert LocalizedText.model_validate(data).model_dump() == data


@pytest.mark.parametrize(
    "cls,field",
    [
        (Title, "main_title"),
        (schemas.AgentCreate, "authorized_name"),
        (schemas.SubjectCreate, "preferred_label"),
    ],
)
def test_unique_language_labels(cls: type[BaseModel], field: str) -> None:
    base = (
        {"kind": "Person"}
        if cls is schemas.AgentCreate
        else {"scheme": [{"uri": "urn:test:scheme"}]}
        if cls is schemas.SubjectCreate
        else {}
    )
    with pytest.raises(ValidationError):
        cls.model_validate(
            dict(
                base,
                **{
                    field: [
                        {"value": "Um", "language": "pt"},
                        {"value": "Outro", "language": "PT"},
                    ]
                },
            )
        )


def test_work_primary_title_and_item_local_identifier() -> None:
    for operation in ("Create", "Read", "Update"):
        for name, field, replacement in (
            (
                "Work",
                "title",
                [{"kind": "VariantTitle", "main_title": [{"value": "Variante"}]}],
            ),
            ("Item", "identified_by", [{"kind": "Identifier", "value": "TEST"}]),
        ):
            data = payload(name, operation)
            data[field] = replacement
            with pytest.raises(ValidationError):
                model(name, operation).model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"agent": {"uri": "urn:test:agent"}, "role": []},
        {"agent": [{"uri": "urn:test:agent"}], "role": [{"uri": "urn:test:role"}]},
        {"agent": {"uri": "urn:test:agent"}},
    ],
)
def test_contribution_cardinality(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Contribution.model_validate(data)


def test_subject_label_disjointness_and_partial_validation() -> None:
    label = [{"value": "Assunto", "language": "pt"}]
    for operation in ("Create", "Read", "Update"):
        data = payload("Subject", operation)
        data.update(preferred_label=label, alternative_label=label)
        with pytest.raises(ValidationError):
            model("Subject", operation).model_validate(data)
    # The service must merge and validate against unchanged labels before committing.
    assert schemas.SubjectUpdate.model_validate(
        {"alternative_label": label}
    ).model_fields_set == {"alternative_label"}


@pytest.mark.parametrize("kind", ["Agent", "Person", "Organization"])
def test_supported_agent_types(kind: str) -> None:
    data = payload("Agent", "Create")
    data["kind"] = kind
    assert schemas.AgentCreate.model_validate(data).kind == kind


@pytest.mark.parametrize(
    "cls,data",
    [
        (Identifier, {"value": 9780000000002}),
        (Identifier, {"value": "x", "source": ["urn:source"]}),
        (Extent, {"label": []}),
        (Note, {"label": []}),
        (Classification, {"classification_portion": []}),
        (ShelfMark, {"value": ""}),
        (Publication, {"date": [2026]}),
        (Title, {"main_title": []}),
    ],
)
def test_auxiliary_structures(cls: type[BaseModel], data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        cls.model_validate(data)


@pytest.mark.parametrize(
    "value,datatype", [("2026", "gYear"), ("2024-02-29", "date"), ("[2026?]", "string")]
)
def test_publication_dates_preserve_lexical_form(value: str, datatype: str) -> None:
    data = {"value": value, "datatype": "http://www.w3.org/2001/XMLSchema#" + datatype}
    assert PublicationDate.model_validate(data).model_dump() == data


@pytest.mark.parametrize(
    "value,datatype",
    [
        ("2026-02-30", "date"),
        ("uncertain", "gYear"),
        (2026, "gYear"),
        ("2026", "integer"),
    ],
)
def test_invalid_publication_dates(value: object, datatype: str) -> None:
    with pytest.raises(ValidationError):
        PublicationDate.model_validate(
            {"value": value, "datatype": "http://www.w3.org/2001/XMLSchema#" + datatype}
        )


def test_isbn_checksum_is_not_an_approved_constraint() -> None:
    assert (
        Identifier(kind="Isbn", value="transcrição sem checksum").value
        == "transcrição sem checksum"
    )


def endpoint(
    cls: type[BaseModel], response: dict[str, object]
) -> Callable[..., object]:
    def handle(body: BaseModel) -> object:
        return response

    handle.__annotations__["body"] = cls
    return handle


def test_fastapi_openapi_and_validation_without_services() -> None:
    app = FastAPI()
    for name in RESOURCES:
        for operation in ("Create", "Read", "Update"):
            cls = model(name, operation)
            app.post(
                f"/api/v1/test/{name.lower()}/{operation.lower()}",
                response_model=cls,
                response_model_exclude_unset=operation == "Update",
            )(endpoint(cls, payload(name, operation)))
    with TestClient(app) as client:
        document = client.get("/openapi.json").json()
        components = document["components"]["schemas"]
        for name in RESOURCES:
            for operation in ("Create", "Read", "Update"):
                path = f"/api/v1/test/{name.lower()}/{operation.lower()}"
                assert name + operation in components
                response = client.post(path, json=payload(name, operation))
                assert response.status_code == 200, response.text
                model(name, operation).model_validate(response.json())
                assert client.post(path, json={"unknown": True}).status_code == 422
        for reference in re.findall(
            r'"\$ref": "#/components/schemas/([^"\s]+)"', json.dumps(document)
        ):
            assert reference in components


def test_contract_properties_match_versioned_profiles() -> None:
    resources = (
        Path(__file__).parents[2]
        / "apps/api/src/libris/modules/bibliographic/resources"
    )
    definitions = {}
    for filename in ("monograph-v1.1.json", "authority-v1.json"):
        for definition in json.loads((resources / filename).read_text())["resources"]:
            definitions[definition["class"].rsplit("/", 1)[-1].rsplit("#", 1)[-1]] = (
                definition
            )
    for name in RESOURCES:
        definition = definitions["Concept" if name == "Subject" else name]
        expected = {
            re.sub(r"(?<!^)([A-Z])", r"_\1", field["name"]).lower()
            for field in definition["fields"]
            if not field["derived"]
        }
        actual = set(model(name, "Create").model_fields) - {"kind"}
        assert actual == expected
        required = {
            re.sub(r"(?<!^)([A-Z])", r"_\1", field["name"]).lower()
            for field in definition["fields"]
            if field["minCount"] > 0
        }
        assert {
            field
            for field, info in model(name, "Create").model_fields.items()
            if info.is_required()
        } - {"kind"} == required


@pytest.mark.parametrize("value", ["urn:test:%zz", "https://example.invalid/%"])
def test_invalid_percent_escapes(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(IRI).validate_python(value)


@pytest.mark.parametrize(
    "value,datatype",
    [
        ("2026Z", "gYear"),
        ("-0456+14:00", "gYear"),
        ("12345", "gYear"),
        ("2000-02-29Z", "date"),
        ("2026-10-11-04:00", "date"),
    ],
)
def test_publication_date_years_and_timezones(value: str, datatype: str) -> None:
    assert (
        PublicationDate.model_validate(
            {"value": value, "datatype": "http://www.w3.org/2001/XMLSchema#" + datatype}
        ).value
        == value
    )


@pytest.mark.parametrize(
    "value,datatype",
    [
        ("0000", "gYear"),
        ("2026+14:01", "gYear"),
        ("1900-02-29", "date"),
        ("2026-13-01", "date"),
        ("2026-10-00", "date"),
        ("2026-10-11+15:00", "date"),
    ],
)
def test_invalid_publication_calendar_and_timezone(value: str, datatype: str) -> None:
    with pytest.raises(ValidationError):
        PublicationDate.model_validate(
            {"value": value, "datatype": "http://www.w3.org/2001/XMLSchema#" + datatype}
        )


@pytest.mark.parametrize("name", RESOURCES)
def test_all_optional_collections_and_multivalues(name: str) -> None:
    create = model(name, "Create")
    for field, info in create.model_fields.items():
        if info.is_required():
            continue
        data = payload(name, "Create")
        data[field] = []
        assert getattr(create.model_validate(data), field) == []
        for operation in ("Create", "Read"):
            invalid = payload(name, operation)
            invalid[field] = None
            with pytest.raises(ValidationError):
                model(name, operation).model_validate(invalid)
        for value in (None, []):
            update = model(name, "Update").model_validate({field: value})
            assert update.model_dump(exclude_unset=True) == {field: value}
    multivalued = payload(name, "Create")
    field = {
        "Work": "language",
        "Instance": "identified_by",
        "Item": "identified_by",
        "Agent": "authorized_name",
        "Subject": "preferred_label",
    }[name]
    current = multivalued.get(field, [])
    assert isinstance(current, list)
    additional = (
        {"value": "English label", "language": "en-GB"}
        if name in {"Agent", "Subject"}
        else {"kind": "Local", "value": "TEST-SECOND"}
        if name in {"Instance", "Item"}
        else {"uri": "http://id.loc.gov/vocabulary/languages/eng"}
    )
    multivalued[field] = [*current, additional]
    assert len(getattr(create.model_validate(multivalued), field)) >= 2


def test_documented_json_examples_are_valid() -> None:
    document = (
        Path(__file__).parents[2] / "docs/architecture/catalogographic-schemas.md"
    ).read_text()
    examples = re.findall(r"```json\n(.*?)\n```", document, re.DOTALL)
    assert len(examples) == 15
    for index, example in enumerate(examples):
        model(
            RESOURCES[index // 3], ("Create", "Read", "Update")[index % 3]
        ).model_validate_json(example)


def test_read_uri_is_not_rebuilt_or_normalized() -> None:
    data = payload("Work", "Read")
    data["uri"] = (
        "https://OLD.example.invalid/resources/00000000-0000-4000-8000-000000000001"
    )
    assert (
        schemas.WorkRead.model_validate(data).model_dump(mode="json")["uri"]
        == data["uri"]
    )


@pytest.mark.parametrize(
    "value", ["https://example.invalid:port/a", "https://example.invalid:65536/a"]
)
def test_invalid_http_port(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(IRI).validate_python(value)
