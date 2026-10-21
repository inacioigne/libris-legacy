"""M06.01: real routes, deterministic baseline and behavioral compatibility."""

import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from libris.api.problems import ProblemDetails
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.bibliographic.api import schemas
from libris.modules.bibliographic.application.catalog import CatalogService
from libris.modules.bibliographic.domain.errors import (
    CatalogCode,
    CatalogError,
    ResourceNotFoundError,
)
from libris.modules.bibliographic.domain.models import StaleRevisionError
from libris.modules.identity.api.dependencies import get_current_actor
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.permissions import Permission
from pydantic import BaseModel, ValidationError
from test_catalogographic_http import actor

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/generate_catalogographic_openapi.py"
BASELINE = ROOT / "docs/architecture/catalogographic-openapi-v2-workspace.json"
spec = importlib.util.spec_from_file_location("openapi_generator", SCRIPT)
assert spec and spec.loader
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def test_baseline_matches_real_routes_models_and_parameters() -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    document = json.loads(BASELINE.read_text())
    assert document == app.openapi()
    actual = {}
    for route in iter_route_contexts(app.routes):
        if isinstance(route.original_route, APIRoute) and route.include_in_schema:
            for method in route.methods:
                actual[route.path_format, method.lower()] = route
    expected = {(path, method) for path, item in document["paths"].items() for method in item}
    assert set(actual) == expected
    ids = []
    for (path, method), route in actual.items():
        op = document["paths"][path][method]
        ids.append(op["operationId"])
        assert op["tags"] == route.tags if route.tags else "tags" not in op
        if route.body_field:
            assert op["requestBody"]["required"] == route.body_field.field_info.is_required()
        assert str(route.status_code or 200) in op["responses"]
        for field in route.dependant.path_params:
            parameter = next(p for p in op["parameters"] if p["name"] == field.alias)
            assert parameter["in"] == "path" and parameter["required"]
            if field.field_info.annotation is int:
                assert parameter["schema"].get("exclusiveMinimum") == 0
        assert route.endpoint.__module__.startswith("libris.")
    assert len(ids) == len(set(ids))
    generator.validate_document(document)


@pytest.mark.parametrize("name", ["Work", "Instance", "Item", "Agent", "Subject"])
@pytest.mark.parametrize("suffix", ["Create", "Read", "Update"])
def test_live_entity_schemas_and_versioned_payloads(name: str, suffix: str) -> None:
    model: type[BaseModel] = getattr(schemas, name + suffix)
    document = json.loads(BASELINE.read_text())
    standalone = model.model_json_schema(
        mode="serialization" if suffix == "Read" else "validation",
        ref_template="#/components/schemas/{model}",
    )
    definitions = standalone.pop("$defs", {})
    # FastAPI's OpenAPI serialization excludes None, including default: null.
    # The documentary Pydantic baseline retains those defaults; runtime is tested too.
    assert jsonable_encoder(standalone, exclude_none=True) == (
        document["components"]["schemas"][model.__name__]
    )
    for key, value in definitions.items():
        assert jsonable_encoder(value, exclude_none=True) == document["components"]["schemas"][key]
    payload = json.loads((ROOT / "tests/fixtures/bibliographic/contracts" /
                          f"{name.lower()}-{suffix.lower()}.json").read_text())
    result = model.model_validate(payload)
    assert model.model_validate(result.model_dump(mode="json", exclude_unset=suffix == "Update"))
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "new_optional_field": "old consumer rejects"})
    if suffix == "Update":
        assert model.model_validate({}).model_dump(exclude_unset=True) == {}
        for field in model.required_fields:
            with pytest.raises(ValidationError):
                model.model_validate({field: None})


def test_generator_is_offline_environment_independent_and_deterministic() -> None:
    with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
        first = generator.serialize(generator.openapi_document())
        with patch.dict("os.environ", {"APP_ENV": "invalid", "OIDC_ISSUER_URL": "invalid"}):
            second = generator.serialize(generator.openapi_document())
    assert first.encode() == second.encode() == BASELINE.read_bytes()


@pytest.mark.parametrize("mutation", [
    "path", "operation", "parameter", "body", "status", "media", "type", "required",
    "nullable", "default", "enum", "constraint", "reference", "security", "documentation",
])
def test_every_unreviewed_drift_fails_with_diff_without_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str,
) -> None:
    current = json.loads(BASELINE.read_text())
    changed = deepcopy(current)
    op = changed["paths"]["/api/v1/catalog/works/{id}"]["patch"]
    model = changed["components"]["schemas"]["WorkUpdate"]
    if mutation == "path":
        del changed["paths"]["/api/v1/catalog/works"]
    elif mutation == "operation":
        del changed["paths"]["/api/v1/catalog/works"]["post"]
    elif mutation == "parameter":
        op["parameters"][0]["required"] = False
    elif mutation == "body":
        op["requestBody"]["required"] = False
    elif mutation == "status":
        del op["responses"]["412"]
    elif mutation == "media":
        del op["responses"]["200"]["content"]["application/json"]
    elif mutation == "type":
        model["properties"]["title"]["type"] = "string"
    elif mutation == "required":
        model["required"] = ["title"]
    elif mutation == "nullable":
        model["properties"]["title"]["type"] = ["array", "null"]
    elif mutation == "default":
        model["properties"]["title"]["default"] = []
    elif mutation == "enum":
        changed["components"]["schemas"]["CatalogCode"]["enum"].append("new.code")
    elif mutation == "constraint":
        model["properties"]["title"]["minItems"] = 2
    elif mutation == "reference":
        op["requestBody"]["content"]["application/json"]["schema"]["$ref"] = (
            "#/components/schemas/WorkCreate"
        )
    elif mutation == "security":
        op["security"] = []
    else:
        op["description"] = "Changed documentation also requires review"
    path = tmp_path / "baseline.json"
    path.write_text(generator.serialize(changed))
    before = path.read_bytes()
    assert not generator.verify(path, generator.serialize(current))
    assert path.read_bytes() == before
    diff = capsys.readouterr().err
    assert "--- baseline/baseline.json" in diff and "+++ runtime/openapi.json" in diff
    assert "@@" in diff


def test_cli_missing_invalid_divergent_and_exclusive_generation(tmp_path: Path) -> None:
    path = tmp_path / "openapi.json"

    def run(mode: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), mode, "--output", str(path)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "APP_ENV": "invalid", "OIDC_ISSUER_URL": "invalid"},
        )

    assert run("--check").returncode == 1
    assert not path.exists()
    assert run("--generate").returncode == 0
    before = path.read_bytes()
    assert run("--generate").returncode == 1
    assert run("--check").returncode == 0
    assert path.read_bytes() == before
    for invalid in ("broken JSON", '{}', before.decode().replace('"0.1.0"', '"changed"')):
        path.write_text(invalid)
        assert run("--check").returncode == 1
        assert path.read_text() == invalid
        assert run("--generate").returncode == 1
        assert path.read_text() == invalid


@pytest.mark.parametrize("error,status,code", [
    (CatalogError(CatalogCode.MALFORMED_REQUEST, "secret"), 400, "request.malformed"),
    (PermissionDenied("secret"), 403, "authorization.denied"),
    (ResourceNotFoundError("secret"), 404, "resource.unknown"),
    (CatalogError(CatalogCode.CONFLICT, "secret"), 409, "state.conflict"),
    (StaleRevisionError("secret"), 412, "revision.stale"),
    (CatalogError(CatalogCode.SCHEMA_INVALID, "secret"), 422, "schema.invalid"),
    (CatalogError(CatalogCode.PRECONDITION_REQUIRED, "secret"),
     428, "revision.precondition_required"),
    (RuntimeError("secret"), 500, "internal.failure"),
    (ConnectionError("secret"), 503, "infrastructure.unavailable"),
])
def test_real_read_route_problem_contract_with_injected_failure(
    error: Exception, status: int, code: str,
) -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_current_actor] = lambda: actor(*Permission)
    path = f"/api/v1/catalog/works/{uuid4()}"
    # Failure injection at existing service, never a simulated endpoint or SQL evidence.
    with TestClient(app, raise_server_exceptions=False) as client, patch.object(
        CatalogService, "current", side_effect=error,
    ):
        response = client.get(path)
    assert response.status_code == status
    problem = ProblemDetails.model_validate(response.json())
    assert problem.status == status and problem.code == code
    assert problem.request_id == response.headers["x-request-id"]
    assert response.headers["content-type"] == "application/problem+json"
    assert "secret" not in response.text
    operation = app.openapi()["paths"]["/api/v1/catalog/works/{id}"]["get"]
    assert str(status) in operation["responses"]
    assert "X-Request-ID" in operation["responses"][str(status)]["headers"]


def test_component_reachability_preserves_real_fastapi_output() -> None:
    document = json.loads(BASELINE.read_text())
    components = document["components"]["schemas"]
    reached: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and value.startswith("#/components/schemas/"):
            name = value.rsplit("/", 1)[-1]
            assert name in components
            if name not in reached:
                reached.add(name)
                visit(components[name])

    visit(document["paths"])
    assert set(components) - reached == {"HTTPValidationError", "ValidationError"}
    assert {
        "ProblemDetails", "CatalogValidationResult", "WorkCreate", "WorkRead", "WorkUpdate",
    } <= reached


def test_missing_precondition_and_unacceptable_format_use_declared_transport() -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_current_actor] = lambda: actor(*Permission)
    path = f"/api/v1/catalog/works/{uuid4()}"
    with TestClient(app) as client, patch.object(CatalogService, "current") as current:
        missing = client.patch(path, json={})
        assert missing.status_code == 428
        current.assert_not_called()
        invalid = client.patch(path, json={}, headers={"If-Match": 'W/"weak"'})
        assert invalid.status_code == 400
        current.assert_not_called()
        unacceptable = client.get(path, headers={"Accept": "image/png"})
        assert unacceptable.status_code == 406
        assert "Accept" in unacceptable.headers["vary"]
        problem = ProblemDetails.model_validate(unacceptable.json())
        assert problem.code == "http.406" and problem.status == 406
        assert problem.request_id == unacceptable.headers["x-request-id"]
    op = app.openapi()["paths"]["/api/v1/catalog/works/{id}"]
    parameter = next(p for p in op["patch"]["parameters"] if p["name"] == "if-match")
    assert parameter["required"] and parameter["schema"]["type"] == "string"
    assert "428" in parameter["description"]
    assert "application/problem+json" in op["get"]["responses"]["406"]["content"]


@pytest.mark.parametrize("reference", ["#/components/schemas/Absent", "https://example.invalid/schema"])
def test_broken_or_remote_references_fail(reference: str) -> None:
    document = json.loads(BASELINE.read_text())
    document["components"]["schemas"]["WorkCreate"]["$ref"] = reference
    with pytest.raises(ValueError):
        generator.validate_document(document)


def test_check_rejects_byte_drift_in_line_endings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    current = BASELINE.read_bytes()
    path = tmp_path / "crlf.json"
    changed = current.replace(b"\n", b"\r\n")
    path.write_bytes(changed)
    assert not generator.verify(path, current.decode("utf-8"))
    assert path.read_bytes() == changed
    assert "Drift OpenAPI" in capsys.readouterr().err
