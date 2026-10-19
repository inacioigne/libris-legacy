"""Issue #31: documentary schemas reflect real contracts, without catalog routes."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from libris.api.problems import ProblemDetails, classify
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.bibliographic.api import schemas
from libris.modules.bibliographic.application.controlled_values import CatalogValueDocument
from libris.modules.bibliographic.application.diagnostics import CatalogValidationResult, Diagnostic
from libris.modules.bibliographic.domain.errors import CatalogCode, CatalogError

ROOT = Path(__file__).parents[2]
ARTIFACT = ROOT / "docs/architecture/catalogographic-contract-v1.schema.json"
PUBLIC_MODELS: tuple[type[BaseModel], ...] = tuple(
    getattr(schemas, name) for name in schemas.__all__
) + (CatalogValidationResult, CatalogValueDocument, ProblemDetails)


@pytest.mark.parametrize("model", PUBLIC_MODELS, ids=lambda model: model.__name__)
def test_documented_schema_matches_live_contract(model: type[BaseModel]) -> None:
    artifact = json.loads(ARTIFACT.read_text())
    standalone = model.model_json_schema(mode="validation")
    definitions = standalone.pop("$defs", {})
    root = artifact["x-libris-roots"][model.__name__]["$ref"].rsplit("/", 1)[-1]
    assert artifact["$defs"][root] == standalone
    for name, definition in definitions.items():
        assert artifact["$defs"][name] == definition


def test_baseline_identity_roots_and_references_are_complete() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    assert artifact["$id"] == "urn:libris:contract:catalogographic:v1:1.0.0"
    assert artifact["x-libris-specification-version"] == "1.0.0"
    assert set(artifact["x-libris-roots"]) == {model.__name__ for model in PUBLIC_MODELS}

    def check_references(value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/$defs/")
                assert reference.removeprefix("#/$defs/") in artifact["$defs"]
            for child in value.values():
                check_references(child)
        elif isinstance(value, list):
            for child in value:
                check_references(child)

    check_references(artifact)


def test_documentary_generator_checks_without_mutating_baseline() -> None:
    paths = [ARTIFACT, ROOT / "docs/architecture/catalogographic-contract-field-inventory.md"]
    before = [path.read_bytes() for path in paths]
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_catalogographic_contract.py"), "--check"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert [path.read_bytes() for path in paths] == before


def test_generator_refuses_to_overwrite_divergent_release(tmp_path: Path) -> None:
    script = tmp_path / "scripts/generate_catalogographic_contract.py"
    script.parent.mkdir()
    script.write_bytes((ROOT / "scripts/generate_catalogographic_contract.py").read_bytes())
    output = tmp_path / "docs/architecture"
    output.mkdir(parents=True)
    baseline = output / ARTIFACT.name
    baseline.write_bytes(b'{"historical": "preserve"}\n')
    before = baseline.read_bytes()
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "Baseline existente divergente" in result.stderr
    assert baseline.read_bytes() == before
    assert not (output / "catalogographic-contract-field-inventory.md").exists()


def test_production_openapi_has_no_documentary_catalog_endpoints() -> None:
    document = create_app(Settings(_env_file=None)).openapi()
    assert set(document["paths"]) == {"/health", "/api/v1/identity/me"} | {
        f"/api/v1/catalog/{collection}{suffix}"
        for collection in ("works", "instances", "items", "agents", "subjects")
        for suffix in ("", "/{id}")
    } | {
        f"/api/v1/catalog/public/{collection}"
        for collection in ("works", "instances", "items", "agents", "subjects")
    } | {
        "/api/v1/catalog/profiles",
        "/api/v1/catalog/profiles/release",
        "/api/v1/catalog/{collection}/validation",
        "/api/v1/catalog/{collection}/{id}/relationships",
        "/api/v1/vocabularies",
        "/api/v1/vocabularies/{vocabulary_id}/concepts",
        "/api/v1/vocabularies/{vocabulary_id}/concepts/{concept_id}",
        "/api/v1/vocabularies/{vocabulary_id}/validate",
    } | {
        "/api/v1/catalog/{collection}/{id}/" + suffix
        for suffix in (
            "editorial", "editorial/submit", "editorial/approve", "editorial/publish",
            "editorial/return", "editorial/withdraw", "editorial/republish", "editorial/archive",
            "public", "revisions", "revisions/compare", "revisions/{number}",
            "revisions/{number}/snapshot", "revisions/restore", "audit", "audit/{event_id}",
            "history",
        )
    }
    assert document["info"]["version"] == "0.1.0"
    assert "WorkCreate" in document["components"]["schemas"]
    assert "ProblemDetails" in document["components"]["schemas"]


def test_old_strict_readers_do_not_accept_additive_fields_or_unknown_codes() -> None:
    payload = json.loads(
        (ROOT / "tests/fixtures/bibliographic/contracts/work-read.json").read_text()
    )
    with pytest.raises(ValidationError) as error:
        schemas.WorkRead.model_validate({**payload, "future_optional_field": []})
    assert any(issue["type"] == "extra_forbidden" for issue in error.value.errors())
    with pytest.raises(ValidationError) as error:
        Diagnostic(code="future.code", message="Exemplo", category="schema")
    assert any(issue["type"] == "enum" for issue in error.value.errors())


@pytest.mark.parametrize(
    "code",
    [
        CatalogCode.REFERENCE_UNVERIFIED,
        CatalogCode.REFERENCE_DEPRECATED,
        CatalogCode.VOCABULARY_MISMATCH,
        CatalogCode.VOCABULARY_UNVERIFIED,
        CatalogCode.RESOLVER_UNAVAILABLE,
        CatalogCode.EDITORIAL_STATE_INVALID,
        CatalogCode.TRANSITION_FORBIDDEN,
        CatalogCode.EDITORIAL_PERMISSION_DENIED,
        CatalogCode.EDITORIAL_PRECONDITION,
        CatalogCode.CONTENT_NOT_PUBLISHABLE,
        CatalogCode.EDITORIAL_VERSION_STALE,
        CatalogCode.APPROVAL_STALE,
        CatalogCode.IDENTITY_OPERATION_INVALID,
    ],
)
def test_extended_domain_errors_keep_current_http_fallback(code: CatalogCode) -> None:
    expected = {
        CatalogCode.EDITORIAL_STATE_INVALID: 409,
        CatalogCode.TRANSITION_FORBIDDEN: 409,
        CatalogCode.EDITORIAL_VERSION_STALE: 412,
    }.get(code, 422)
    assert classify(CatalogError(code, "Exemplo interno")) == (expected, code.value)
