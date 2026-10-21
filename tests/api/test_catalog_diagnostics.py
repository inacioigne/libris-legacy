"""Public diagnostics integrated with the real validators, service and HTTP app."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from libris.api.problems import adapt_pydantic_error, require_if_match
from libris.core.config import Settings
from libris.core.correlation import request_id_context
from libris.main import create_app
from libris.modules.bibliographic.api.schemas import WorkCreate
from libris.modules.bibliographic.application.diagnostics import (
    CatalogValidationRejected,
    CatalogValidationResult,
)
from libris.modules.bibliographic.application.service import (
    BibliographicService,
    prepare_document,
)
from libris.modules.bibliographic.application.validation import validate_catalog
from libris.modules.bibliographic.domain.errors import (
    CatalogCode,
    CatalogError,
    ResourceNotFoundError,
)
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    StaleRevisionError,
)
from libris.modules.bibliographic.domain.profile_contract import (
    ProfileValidationContext,
)
from libris.modules.bibliographic.domain.profile_registry import ProfileError
from libris.modules.bibliographic.infrastructure import validation
from libris.modules.bibliographic.infrastructure.diagnostics import adapt_report
from libris.modules.bibliographic.infrastructure.profiles import (
    load_legacy_profile,
    load_profile_registry,
)
from libris.modules.bibliographic.infrastructure.rdf import (
    BF,
    RdfDocumentError,
    parse_document,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    PROFILE_ID,
    ShaclValidationError,
    ValidationIssue,
    ValidationReport,
)
from libris.modules.identity.application.authorization import TechnicalContext
from libris.modules.identity.domain.authorization import Operation
from libris.modules.identity.domain.permissions import Permission as ContextPermission
from pydantic import ValidationError
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, RDFS, SH

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


FIXTURES = Path(__file__).parents[1] / "fixtures/bibliographic"
CORE = "urn:libris:profile:core:2.0.0"


def work(*, valid: bool = True) -> Graph:
    graph = Graph()
    graph.add((URIRef("urn:w"), RDF.type, BF.Work))
    if valid:
        title = BNode()
        graph.add((URIRef("urn:w"), BF.title, title))
        graph.add((title, RDF.type, BF.Title))
        graph.add((title, BF.mainTitle, Literal("Título sintético", lang="pt")))
    return graph


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("profile", [PROFILE_ID, INSTITUTIONAL_PROFILE_ID, CORE])
def test_real_validation_and_immutable_release(profile: str, valid: bool) -> None:
    selected = load_profile_registry().resolve(profile) if profile == CORE else profile
    result = validate_catalog(work(valid=valid), selected)
    assert result.state == "completed"
    assert result.conforms is valid
    assert result.profile_release == profile
    assert result.profile_version == (
        "2.0.0" if profile == CORE else ("1.1" if profile == INSTITUTIONAL_PROFILE_ID else "1.0")
    )
    assert result.context == "snapshot"
    assert bool(result.issues) is not valid
    assert "request_id" not in result.public_dict()
    if not valid:
        assert any(issue.field_ids == (str(BF.Work) + "/title",) for issue in result.issues)
        assert any(issue.code == CatalogCode.MIN_COUNT for issue in result.issues)


def test_authority_and_institutional_fixtures() -> None:
    person = Graph()
    person.add((URIRef("urn:person"), RDF.type, BF.Person))
    person.add((URIRef("urn:person"), RDFS.label, Literal("Pessoa sintética")))
    assert validate_catalog(person, AUTHORITY_PROFILE_ID).conforms is True
    person.remove((None, RDFS.label, None))
    assert validate_catalog(person, AUTHORITY_PROFILE_ID).conforms is False
    graph = parse_document((FIXTURES / "institutional/single-author.ttl").read_text(), "turtle")
    assert validate_catalog(graph, INSTITUTIONAL_PROFILE_ID).conforms is True
    graph = parse_document(
        (FIXTURES / "institutional/invalid-missing-title.ttl").read_text(), "turtle"
    )
    assert validate_catalog(graph, INSTITUTIONAL_PROFILE_ID).conforms is False


@pytest.mark.parametrize("context", ["snapshot", "composed"])
def test_context_and_profile_policy(context: str) -> None:
    profile = load_profile_registry().resolve("urn:libris:profile:digital:2.0.0")
    graph = Graph()
    graph.add((URIRef("urn:i"), RDF.type, BF.Instance))
    graph.add((URIRef("urn:i"), BF.instanceOf, URIRef("urn:w")))
    graph.add(
        (
            URIRef("urn:i"),
            URIRef("https://schema.org/encodingFormat"),
            Literal("invalid"),
        )
    )
    result = validate_catalog(graph, profile, context=context)
    assert result.context == context
    assert result.conforms is False
    assert result.profile_release == profile.release_id
    assert any(issue.code == CatalogCode.IN for issue in result.issues)


@pytest.mark.parametrize("code", ["engine_failure", "shapes_unavailable", "malformed_shapes"])
def test_technical_failure_is_distinct(code: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> Graph:
        raise ShaclValidationError(code, "secret password /private/file SELECT *")

    monkeypatch.setattr(validation, "_load_shapes", fail)
    result = validate_catalog(work(), INSTITUTIONAL_PROFILE_ID)
    assert result.state == "failed"
    assert result.conforms is None
    assert "conforms" not in result.public_dict()
    assert result.issues[0].category == "technical"
    assert "secret" not in result.public_json()
    with pytest.raises(ShaclValidationError):
        prepare_document(work(), "urn:w", INSTITUTIONAL_PROFILE_ID)


def test_request_correlation_in_service_and_rejection() -> None:
    identifier = str(uuid4())
    token = request_id_context.set(identifier)
    try:
        with pytest.raises(CatalogValidationRejected) as error:
            prepare_document(work(valid=False), "urn:w", INSTITUTIONAL_PROFILE_ID)
        assert isinstance(error.value, ValueError)
        assert error.value.result.request_id == identifier
        assert validate_catalog(work(), INSTITUTIONAL_PROFILE_ID).request_id == identifier
    finally:
        request_id_context.reset(token)


@pytest.mark.parametrize("severity", [SH.Violation, SH.Warning, SH.Info])
def test_real_engine_severities(severity: URIRef, monkeypatch: pytest.MonkeyPatch) -> None:
    shapes = Graph()
    shape = URIRef("urn:test:shape")
    shapes.add((shape, RDF.type, SH.PropertyShape))
    shapes.add((shape, SH.targetClass, BF.Work))
    shapes.add((shape, SH.path, BF.language))
    shapes.add((shape, SH.minCount, Literal(1)))
    shapes.add((shape, SH.severity, severity))
    monkeypatch.setattr(validation, "_load_shapes", lambda names: shapes)
    report = validation.validate_profile(work(), INSTITUTIONAL_PROFILE_ID)
    result = adapt_report(report, load_legacy_profile(INSTITUTIONAL_PROFILE_ID), work())
    assert result.conforms is (severity != SH.Violation)
    assert result.issues[0].severity == str(severity).split("#")[-1]
    assert result.issues[0].constraint == str(SH.MinCountConstraintComponent)
    assert result.issues[0].source_shape == str(shape)


def issue_report(
    graph: Graph,
    *,
    component: str | None = None,
    path: URIRef | BNode | None = BF.mainTitle,
    shape: URIRef | BNode | None = None,
) -> ValidationReport:
    issue = ValidationIssue(
        "urn:focus",
        str(path) if path else None,
        str(SH.Violation),
        ("secret password /private/file SELECT *",),
        source_shape=str(shape) if shape else None,
        source_constraint_component=component,
        value="personal@example.invalid",
        focus_term=URIRef("urn:focus"),
        path_term=path,
        source_shape_term=shape,
        value_term=Literal("personal@example.invalid"),
    )
    return ValidationReport(False, (issue,), graph)


def test_multiple_fields_and_global_diagnostics() -> None:
    profile = load_profile_registry().resolve(CORE)
    graph = Graph()
    focus = URIRef("urn:focus")
    graph.add((focus, RDF.type, BF.Title))
    graph.add((focus, RDF.type, BF.VariantTitle))
    result = adapt_report(issue_report(Graph()), profile, graph)
    assert result.issues[0].field_ids == (
        str(BF.Title) + "/mainTitle",
        str(BF.VariantTitle) + "/mainTitle",
    )
    global_result = adapt_report(
        issue_report(Graph(), path=URIRef("urn:unknown:property")), profile, graph
    )
    assert global_result.issues[0].field_ids == ()
    assert "personal@" not in result.public_json()
    assert "secret" not in result.public_json()
    assert result.issues[0].code == CatalogCode.SHACL
    assert "constraint" not in result.issues[0].public_dict()
    assert "source_shape" not in result.issues[0].public_dict()
    assert "value" not in result.issues[0].public_dict()


@pytest.mark.parametrize(
    "component,expected",
    [
        (str(SH.MinCountConstraintComponent), CatalogCode.MIN_COUNT),
        (str(SH.MaxCountConstraintComponent), CatalogCode.MAX_COUNT),
        (str(SH.DatatypeConstraintComponent), CatalogCode.DATATYPE),
        ("urn:custom:constraint", CatalogCode.SHACL),
        (None, CatalogCode.SHACL),
    ],
)
def test_component_codes_do_not_depend_on_messages(
    component: str | None, expected: CatalogCode
) -> None:
    result = adapt_report(
        issue_report(Graph(), component=component),
        load_legacy_profile(PROFILE_ID),
        Graph(),
    )
    assert result.issues[0].code == expected
    assert result.issues[0].constraint == component


@pytest.mark.parametrize(
    "kind",
    [
        "sequence",
        "alternative",
        "inverse",
        "zero_or_more",
        "one_or_more",
        "zero_or_one",
        "opaque",
    ],
)
def test_complex_paths(kind: str) -> None:
    graph = Graph()
    root = BNode()
    if kind in {"sequence", "alternative"}:
        sequence = root if kind == "sequence" else BNode()
        Collection(graph, sequence, [BF.title, BF.mainTitle])
        if kind == "alternative":
            graph.add((root, SH.alternativePath, sequence))
    elif kind != "opaque":
        predicate = {
            "inverse": SH.inversePath,
            "zero_or_more": SH.zeroOrMorePath,
            "one_or_more": SH.oneOrMorePath,
            "zero_or_one": SH.zeroOrOnePath,
        }[kind]
        graph.add((root, predicate, BF.title))
    result = adapt_report(
        issue_report(graph, path=root), load_profile_registry().resolve(CORE), Graph()
    )
    diagnostic = result.issues[0]
    assert diagnostic.path is not None and diagnostic.path.kind == kind
    assert diagnostic.field_ids == ()
    if kind == "sequence":
        assert [item.iri for item in diagnostic.path.items] == [
            str(BF.title),
            str(BF.mainTitle),
        ]


def test_stable_serialization_under_blank_node_renaming_and_engine_order() -> None:
    results: list[str] = []
    for _ in range(2):
        graph = work()
        report = validation.validate_profile(graph, INSTITUTIONAL_PROFILE_ID)
        # Multiple violations on an auxiliary focus with sensitive literals.
        title = graph.value(URIRef("urn:w"), BF.title)
        graph.add((title, BF.mainTitle, Literal("Outro título", lang="pt")))
        graph.add(
            (
                title,
                BF.mainTitle,
                Literal("Sensitive", datatype=URIRef("urn:unknown:datatype")),
            )
        )
        report = validation.validate_profile(graph, INSTITUTIONAL_PROFILE_ID)
        if results:
            report = ValidationReport(report.conforms, tuple(reversed(report.issues)), report.graph)
        result = adapt_report(report, load_legacy_profile(INSTITUTIONAL_PROFILE_ID), graph)
        assert len(result.issues) > 1
        results.append(result.public_json())
    assert results[0] == results[1]


def test_schema_adapter_uses_pydantic_contract_and_omits_input() -> None:
    with pytest.raises(ValidationError) as error:
        WorkCreate.model_validate({"title": [], "secret_password": "do-not-echo"})
    diagnostics = adapt_pydantic_error(error.value)
    assert any(issue.field_ids == ("/title",) for issue in diagnostics)
    serialized = json.dumps([issue.public_dict() for issue in diagnostics])
    assert "secret_password" not in serialized and "do-not-echo" not in serialized
    assert any(issue.code == CatalogCode.CARDINALITY for issue in diagnostics)


@pytest.mark.parametrize(
    "state,conforms", [("failed", True), ("failed", False), ("completed", None)]
)
def test_result_invariants(state: str, conforms: bool | None) -> None:
    with pytest.raises(ValidationError):
        CatalogValidationResult(
            state=state,
            conforms=conforms,
            profile_id="urn:p",
            profile_release="urn:p:1",
            profile_version="1.0",
            context="snapshot",
        )


@pytest.mark.parametrize(
    "error,status,code",
    [
        (RdfDocumentError("secret"), 400, "request.malformed"),
        (ResourceNotFoundError("secret"), 404, "resource.unknown"),
        (ProfileError("unknown_profile", "secret"), 404, "profile.unknown"),
        (ProfileError("unknown_release", "secret"), 404, "profile.unknown"),
        (CatalogError(CatalogCode.CONFLICT, "secret"), 409, "state.conflict"),
        (StaleRevisionError("secret"), 412, "revision.stale"),
        (ProfileError("inapplicable_profile", "secret"), 422, "profile.incompatible"),
        (
            CatalogError(CatalogCode.PRECONDITION_REQUIRED, "secret"),
            428,
            "revision.precondition_required",
        ),
        (ShaclValidationError("engine_failure", "secret"), 500, "validator.failure"),
        (RuntimeError("secret"), 500, "internal.failure"),
        (ValueError("secret"), 500, "internal.failure"),
        (KeyError("secret"), 500, "internal.failure"),
        (
            ShaclValidationError("shapes_unavailable", "secret"),
            503,
            "infrastructure.unavailable",
        ),
        (ProfileError("profile_unavailable", "secret"), 503, "profile.unavailable"),
    ],
)
def test_http_mapping_and_security(error: Exception, status: int, code: str) -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))

    @app.get("/test")
    def fail() -> None:
        raise error

    identifier = str(uuid4())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test?password=secret", headers={"X-Request-ID": identifier})
    body = response.json()
    assert response.status_code == status == body["status"]
    assert response.headers["content-type"] == "application/problem+json"
    assert body["request_id"] == response.headers["x-request-id"] == identifier
    assert body["instance"] == f"urn:uuid:{identifier}"
    assert body["code"] == code
    assert body["type"] == f"urn:libris:problem:v1:{code}"
    assert "secret" not in response.text


def test_real_http_schema_errors_openapi_and_authentication() -> None:
    app = create_app(Settings(_env_file=None))

    @app.post("/test/schema")
    def create(data: WorkCreate) -> WorkCreate:
        return data

    with TestClient(app) as client:
        missing = client.post("/test/schema", json={})
        assert missing.status_code == 422
        assert missing.json()["issues"][0]["field_ids"] == ["/title"]
        assert missing.json()["issues"][0]["code"] == "schema.required"
        assert (
            client.post(
                "/test/schema", content="{", headers={"Content-Type": "application/json"}
            ).status_code
            == 400
        )
        extra = client.post("/test/schema", json={"title": [], "secret": "private"})
        assert "secret" not in extra.text and "private" not in extra.text
        auth = client.get("/api/v1/identity/me")
        assert auth.status_code == 401
        assert auth.headers["www-authenticate"] == "Bearer"
        assert auth.json()["code"] == "authentication.required"
        assert client.get("/missing").status_code == 404
        schema = client.get("/openapi.json").json()
    assert "CatalogValidationResult" in schema["components"]["schemas"]
    assert "HTTPValidationError" not in schema["components"]["schemas"] or (
        schema["paths"]["/test/schema"]["post"]["responses"]["422"]["content"]
        == {"application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}}
    )
    for status in [400, 404, 409, 412, 422, 428, 500, 503]:
        assert (
            "application/problem+json"
            in schema["paths"]["/health"]["get"]["responses"][str(status)]["content"]
        )


def test_http_rejection_includes_real_diagnostics_and_single_request_id() -> None:
    app = create_app(Settings(_env_file=None))

    @app.post("/test/validate")
    def reject() -> None:
        prepare_document(work(valid=False), "urn:w", INSTITUTIONAL_PROFILE_ID)

    with TestClient(app) as client:
        response = client.post("/test/validate", headers={"X-Request-ID": "invalid"})
    assert response.status_code == 422
    body = response.json()
    assert body["validation"]["state"] == "completed"
    assert body["validation"]["conforms"] is False
    assert body["issues"] == body["validation"]["issues"]
    assert (
        body["request_id"] == body["validation"]["request_id"] == response.headers["x-request-id"]
    )
    assert str(uuid4()) != body["request_id"]
    assert request_id_context.get() is None


@pytest.mark.parametrize(
    "value,status",
    [
        (None, 428),
        ('"old-edit-v1"', 412),
        ("*", 400),
        ('W/"edit-v1"', 400),
        ('"edit-v1", "old"', 400),
        ("uuid:r1", 400),
        ('"edit-v1"', 200),
    ],
)
def test_http_preconditions(value: str | None, status: int) -> None:
    app = create_app(Settings(_env_file=None))

    @app.post("/test/write")
    def write(if_match: str | None = Header(default=None)) -> dict[str, str]:
        require_if_match(if_match, current_etag='"edit-v1"')
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.post(
            "/test/write", headers={"If-Match": value} if value is not None else {}
        )
    assert response.status_code == status


def test_invalid_create_never_opens_transaction() -> None:
    class Sessions:
        def __call__(self) -> None:
            pytest.fail("Invalid data must fail before a transaction is opened")

    service = BibliographicService(Sessions())
    identity = ResourceIdentity.create("https://example.invalid/resources")
    graph = Graph()
    graph.add((URIRef(identity.uri), RDF.type, BF.Work))
    with pytest.raises(CatalogValidationRejected):
        asyncio.run(
            service.create(
                identity,
                graph,
                Provenance("fixture", "test"),
                profile=INSTITUTIONAL_PROFILE_ID,
                context=TEST_CONTEXT,
            )
        )


def test_unknown_and_incompatible_profiles_keep_typed_errors() -> None:
    with pytest.raises(ProfileError) as error:
        validate_catalog(work(), "urn:unknown:profile")
    assert error.value.code == "unknown_profile"
    with pytest.raises(CatalogError) as error:
        prepare_document(work(), "urn:w", AUTHORITY_PROFILE_ID)
    assert error.value.code == CatalogCode.PROFILE_INCOMPATIBLE
    profile = load_profile_registry().resolve(CORE)
    with pytest.raises(ProfileError) as error:
        validation.validate_resolved_profile(
            work(), profile, ProfileValidationContext(profile_release="urn:other")
        )
    assert error.value.code == "validation_release_mismatch"


@pytest.mark.parametrize(
    "value,allowed",
    [
        ("http://id.loc.gov/ontologies/bibframe/title", True),
        ("https://schema.org/dateCreated", True),
        ("urn:example:identity", True),
        ("https://user:password@example.invalid/private", False),
        ("https://example.invalid/?password=secret", False),
        ("file:///private/path", False),
        ("mailto:person@example.invalid", False),
        ("https://example.invalid/" + "a" * 2048, False),
        ("https://example.invalid/\nsecret", False),
    ],
)
def test_value_policy_preserves_only_safe_iris(value: str, allowed: bool) -> None:
    from libris.modules.bibliographic.infrastructure.diagnostics import safe_iri

    assert safe_iri(value) == (value if allowed else None)


def test_anonymous_identifier_does_not_hash_sensitive_lexical_values() -> None:
    from libris.modules.bibliographic.infrastructure.diagnostics import ReportTerms

    identifiers: list[str | None] = []
    for literal in ("personal@example.invalid", "different-secret"):
        graph = Graph()
        focus = BNode()
        graph.add((focus, RDF.type, BF.Title))
        graph.add((focus, BF.mainTitle, Literal(literal)))
        identifiers.append(ReportTerms(graph).identifier(focus))
    assert identifiers[0] == identifiers[1]
    assert identifiers[0] is not None


@pytest.mark.parametrize(
    "sqlstate,invalidated,status",
    [
        ("23505", False, 409),
        ("23503", False, 409),
        ("08006", False, 503),
        ("57P01", False, 503),
        ("42601", False, 500),
        (None, True, 503),
    ],
)
def test_database_error_classification(
    sqlstate: str | None, invalidated: bool, status: int
) -> None:
    from libris.api.problems import classify
    from sqlalchemy.exc import DBAPIError, IntegrityError

    class DriverError(Exception):
        pass

    original = DriverError("secret query")
    original.sqlstate = sqlstate
    kind = IntegrityError if sqlstate in {"23505", "23503"} else DBAPIError
    error = kind(
        "secret SELECT", {"secret": "private"}, original, connection_invalidated=invalidated
    )
    assert classify(error)[0] == status


def test_http_adaptation_reuses_request_id_for_precomputed_report() -> None:
    app = create_app(Settings(_env_file=None))
    result = validate_catalog(work(valid=False), INSTITUTIONAL_PROFILE_ID, request_id=str(uuid4()))

    @app.get("/test/precomputed")
    def fail() -> None:
        raise CatalogValidationRejected(result)

    with TestClient(app) as client:
        response = client.get("/test/precomputed")
    body = response.json()
    assert (
        body["request_id"] == body["validation"]["request_id"] == response.headers["x-request-id"]
    )
    assert result.request_id != body["request_id"]  # Original immutable report remains unchanged.
