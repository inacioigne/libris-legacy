"""Issue #21: execution, diagnostics, policies and unchanged local RDF."""

import asyncio
import json
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from libris.modules.bibliographic.application.service import (
    BibliographicService,
    prepare_document,
)
from libris.modules.bibliographic.domain.models import Provenance, ResourceIdentity
from libris.modules.bibliographic.domain.profile_contract import (
    ProfileValidationContext,
    ResolvedProfile,
)
from libris.modules.bibliographic.domain.profile_registry import ProfileError
from libris.modules.bibliographic.infrastructure import validation
from libris.modules.bibliographic.infrastructure.profiles import load_profile_registry
from libris.modules.bibliographic.infrastructure.rdf import BF, parse_document
from libris.modules.bibliographic.infrastructure.validation import (
    INSTITUTIONAL_PROFILE_ID,
    ShaclValidationError,
    correlate_validation_issues,
    validate_profile,
    validate_resolved_profile,
)
from libris.modules.bibliographic.infrastructure.validation_policy import (
    add_profile_constraints,
)
from libris.modules.identity.application.authorization import TechnicalContext
from libris.modules.identity.domain.authorization import Operation
from libris.modules.identity.domain.permissions import Permission as ContextPermission
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, RDF, RDFS, SH, XSD

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


CORE = "urn:libris:profile:core:2.0.0"
PREFIXES = """@prefix bf: <http://id.loc.gov/ontologies/bibframe/> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


def rdf(body: str) -> Graph:
    return parse_document(PREFIXES + body, "turtle")


def resolved(profile: ResolvedProfile, graph: Graph) -> validation.ValidationReport:
    return validate_resolved_profile(
        graph, profile, ProfileValidationContext(profile_release=profile.release_id)
    )


@pytest.mark.parametrize(
    "body,valid",
    [
        ('<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "Título"@pt, "Title"@en].', True),
        ("<urn:w> a bf:Work.", False),
        ("<urn:i> a bf:Instance; bf:instanceOf <https://external.invalid/work>.", True),
        ("<urn:i> a bf:Instance; bf:instanceOf <urn:w>, <urn:w2>.", False),
        (
            '<urn:t> a bf:Item; bf:itemOf <urn:i>; bf:identifiedBy [a bf:Local; rdf:value "A"].',
            True,
        ),
        (
            '<urn:t> a bf:Item; bf:itemOf <urn:i>; bf:identifiedBy [a bf:Local; rdf:value ""].',
            False,
        ),
        ('<urn:a> a bf:Agent; rdfs:label "Agente"@pt.', True),
        ('<urn:a> a bf:Person; rdfs:label "Pessoa".', True),
        ('<urn:a> a bf:Organization; rdfs:label "Instituição".', True),
        ("<urn:a> a bf:Person; rdfs:label 42.", False),
        ('<urn:s> a skos:Concept; skos:prefLabel "Tema"@pt; skos:inScheme <urn:scheme>.', True),
        (
            '<urn:s> a skos:Concept; skos:prefLabel "Tema"@pt, "Outro"@pt; '
            "skos:inScheme <urn:scheme>.",
            False,
        ),
        ('<urn:s> a skos:Concept; skos:prefLabel "Tema".', False),
        (
            '<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "T"]; '
            "bf:contribution [a bf:Contribution; bf:agent <urn:a>; bf:role <urn:role>].",
            True,
        ),
        (
            '<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "T"]; '
            "bf:contribution [a bf:Contribution; bf:agent <urn:a>].",
            False,
        ),
        (
            "<urn:i> a bf:Instance; bf:instanceOf <urn:w>; bf:provisionActivity "
            '[a bf:Publication; bf:date "[entre 1900 e 1910]"]; bf:note '
            '[a bf:Note; rdfs:label "N"].',
            True,
        ),
        (
            "<urn:i> a bf:Instance; bf:instanceOf <urn:w>; bf:extent [a bf:Extent; rdfs:label 10].",
            False,
        ),
        (
            "<urn:i> a bf:Instance; bf:instanceOf <urn:w>; bf:identifiedBy "
            '[a bf:Isbn; rdf:value "transcrito"].',
            True,
        ),
        (
            "<urn:i> a bf:Instance; bf:instanceOf <urn:w>; bf:identifiedBy "
            '[a bf:Isbn; rdf:value "A", "B"].',
            False,
        ),
        ('<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "A"@pt, "B"@pt].', False),
    ],
)
def test_entities_and_auxiliaries(body: str, valid: bool) -> None:
    graph = rdf(body)
    before = set(graph)
    assert validate_profile(graph, INSTITUTIONAL_PROFILE_ID).conforms is valid
    assert set(graph) == before


@pytest.mark.parametrize("uri", ["urn:local:work", "https://external.invalid/work"])
def test_references_never_download_or_supply_rules(
    uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    graph = rdf(f"<urn:i> a bf:Instance; bf:instanceOf <{uri}>.")
    graph.add((URIRef("urn:i"), SH.shapesGraph, URIRef("https://external.invalid/shapes")))
    graph.add((URIRef("urn:i"), OWL.imports, URIRef("https://external.invalid/ontology")))
    graph.add((URIRef("urn:injected"), RDF.type, SH.NodeShape))
    graph.add((URIRef("urn:injected"), SH.targetClass, BF.Instance))
    graph.add((URIRef("urn:injected"), SH["class"], BF.Work))
    assert validate_profile(graph, INSTITUTIONAL_PROFILE_ID).conforms
    graph.add((URIRef(uri), RDF.type, BF.Person))
    graph.add((URIRef(uri), RDFS.label, Literal("Pessoa")))
    assert not validate_profile(graph, INSTITUTIONAL_PROFILE_ID, composed=True).conforms


@pytest.mark.parametrize(
    "severities,conforms",
    [
        ((SH.Warning,), True),
        ((SH.Info,), True),
        ((SH.Violation,), False),
        ((SH.Warning, SH.Info), True),
        ((SH.Warning, SH.Info, SH.Violation), False),
    ],
)
def test_severities_and_exact_diagnostics(
    severities: tuple[URIRef, ...], conforms: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    shapes = Graph()
    for severity in severities:
        rule = BNode()
        shapes.add((rule, RDF.type, SH.PropertyShape))
        shapes.add((rule, SH.targetClass, BF.Work))
        shapes.add((rule, SH.path, BF.language))
        shapes.add((rule, SH.nodeKind, SH.IRI))
        shapes.add((rule, SH.severity, severity))
        shapes.add((rule, SH.message, Literal("Idioma", lang="pt")))
    monkeypatch.setattr(validation, "_load_shapes", lambda names: shapes)
    graph = rdf('<urn:w> a bf:Work; bf:language "pt".')
    report = validate_profile(graph, INSTITUTIONAL_PROFILE_ID)
    assert report.conforms is conforms
    assert {issue.severity for issue in report.issues} == {str(s) for s in severities}
    for issue in report.issues:
        assert issue.source_constraint_component == str(SH.NodeKindConstraintComponent)
        assert issue.source_shape_term in set(shapes.subjects(RDF.type, SH.PropertyShape))
        assert issue.value_term == Literal("pt")
        assert issue.focus_term == URIRef("urn:w")
        assert issue.message_terms == (Literal("Idioma", lang="pt"),)
        assert (None, SH.sourceShape, issue.source_shape_term) in report.graph


def test_blank_node_iri_collision_and_complex_path_do_not_correlate() -> None:
    profile = load_profile_registry().resolve(CORE)
    graph = Graph()
    focus = BNode("urn:collision")
    graph.add((focus, RDF.type, BF.Title))
    graph.add((URIRef("urn:collision"), RDF.type, BF.Identifier))
    issue = validation.ValidationIssue(
        str(focus),
        str(BF.mainTitle),
        str(SH.Violation),
        (),
        focus_term=focus,
        path_term=BF.mainTitle,
    )
    report = validation.ValidationReport(False, (issue,), Graph())
    assert correlate_validation_issues(report, profile, graph)[0].field_ids == (
        str(BF.Title) + "/mainTitle",
    )
    complex_issue = validation.ValidationIssue(
        str(focus),
        str(BF.mainTitle),
        str(SH.Violation),
        (),
        focus_term=focus,
        path_term=BNode("path"),
    )
    assert (
        correlate_validation_issues(
            validation.ValidationReport(False, (complex_issue,), Graph()), profile, graph
        )[0].field_ids
        == ()
    )


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", "shapes_unavailable"),
        ("syntax", "malformed_shapes"),
        ("constraint", "malformed_shapes"),
        ("runtime", "engine_failure"),
        ("failure_report", "engine_failure"),
    ],
)
def test_infrastructure_failures_are_not_nonconformance(
    case: str, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if case in {"missing", "syntax"}:
        resource = MagicMock()
        if case == "missing":
            resource.joinpath.return_value.read_text.side_effect = FileNotFoundError()
        else:
            resource.joinpath.return_value.read_text.return_value = "not turtle {"
        monkeypatch.setattr(validation, "files", lambda package: resource)
    elif case == "constraint":
        shapes = rdf(
            "<urn:rule> a <http://www.w3.org/ns/shacl#PropertyShape>; "
            "<http://www.w3.org/ns/shacl#path> bf:title; "
            '<http://www.w3.org/ns/shacl#minCount> "bad".'
        )
        monkeypatch.setattr(validation, "_load_shapes", lambda names: shapes)
    else:
        actual = validation.validate

        def execute(*args: object, **kwargs: object) -> object:
            if kwargs.get("meta_shacl"):
                return actual(*args, **kwargs)
            if case == "runtime":
                raise RuntimeError("engine failed")
            return False, object(), "ValidationFailure"

        monkeypatch.setattr(validation, "validate", execute)
    with pytest.raises(ShaclValidationError) as error:
        validate_profile(Graph(), INSTITUTIONAL_PROFILE_ID)
    assert error.value.code == code


@pytest.mark.parametrize("case", ["profile", "release", "context", "empty"])
def test_invalid_resolution(case: str) -> None:
    profile = load_profile_registry().resolve(CORE)
    if case == "profile":
        with pytest.raises(ProfileError, match="desconhecido"):
            validate_profile(Graph(), "https://external.invalid/shapes")
        return
    data = profile.model_dump()
    if case == "release":
        data["validation"][0]["release"] = "999.0"
    elif case == "context":
        data["validation"] = [
            {"id": "urn:libris:shapes:experimental", "release": "1.0", "context": "snapshot"}
        ]
    else:
        data["validation"] = []
    mode = "composed" if case == "context" else "snapshot"
    with pytest.raises(ProfileError):
        validate_resolved_profile(
            Graph(),
            ResolvedProfile.model_validate(data),
            ProfileValidationContext(profile_release=CORE, mode=mode),
        )


def test_empty_graph_conformance_does_not_authorize_persistence() -> None:
    assert validate_profile(Graph(), INSTITUTIONAL_PROFILE_ID).conforms
    with pytest.raises(ValueError):
        prepare_document(Graph(), "urn:root", INSTITUTIONAL_PROFILE_ID)


@pytest.mark.parametrize(
    "name",
    json.loads(
        (
            Path(__file__).parents[2]
            / "apps/api/src/libris/modules/bibliographic/resources/profiles-v2/manifest.json"
        ).read_text()
    ),
)
def test_all_configurable_releases_execute(name: str) -> None:
    release = json.loads(
        (
            Path(__file__).parents[2]
            / "apps/api/src/libris/modules/bibliographic/resources/profiles-v2"
            / name
        ).read_text()
    )["release_id"]
    profile = load_profile_registry().resolve(release)
    assert resolved(profile, Graph()).conforms


def test_specialized_minimum_enum_datatype_and_optional_extension() -> None:
    registry = load_profile_registry()
    digital = registry.resolve("urn:libris:profile:digital:2.0.0")
    graph = rdf("<urn:i> a bf:Instance; bf:instanceOf <urn:w>.")
    assert resolved(digital, graph).conforms
    encoding = URIRef("https://schema.org/encodingFormat")
    graph.add((URIRef("urn:i"), encoding, Literal("application/pdf")))
    assert resolved(digital, graph).conforms
    graph.set((URIRef("urn:i"), encoding, Literal("invalid")))
    assert not resolved(digital, graph).conforms
    # Historical rules still permit this extension and do not acquire new policy.
    assert validate_profile(graph, INSTITUTIONAL_PROFILE_ID).conforms
    core = registry.resolve(CORE)
    data = core.model_dump()
    work = next(r for r in data["resources"] if r["rdf_class"] == str(BF.Work))
    next(f for f in work["fields"] if f["name"] == "title")["policy"]["min_count"] = 2
    graph = rdf('<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "T"].')
    assert not resolved(ResolvedProfile.model_validate(data), graph).conforms
    graph.add((URIRef("urn:w"), URIRef("https://schema.org/dateCreated"), Literal(42)))
    assert not resolved(digital, graph).conforms


def test_language_policy_and_deterministic_isolated_supplement() -> None:
    profile = load_profile_registry().resolve(CORE)
    data = profile.model_dump()
    title = next(r for r in data["resources"] if r["rdf_class"] == str(BF.Title))
    title["fields"][0]["value"]["language"].update(required=True, allowed=["pt", "en"])
    profile = ResolvedProfile.model_validate(data)
    for language, valid in [("pt-BR", True), ("en", True), ("fr", False), (None, False)]:
        graph = rdf("<urn:w> a bf:Work; bf:title _:t. _:t a bf:Title.")
        graph.add(
            (next(graph.objects(None, BF.title)), BF.mainTitle, Literal("Título", lang=language))
        )
        assert resolved(profile, graph).conforms is valid
    first, second = Graph(), Graph()
    add_profile_constraints(first, profile)
    add_profile_constraints(second, profile)
    assert isomorphic(first, second)
    first.remove((None, None, None))
    assert len(second) > 0


@pytest.mark.parametrize("change", ["minimum", "maximum", "node_kind"])
def test_contradictory_descriptor_rejected_before_engine(change: str) -> None:
    profile = load_profile_registry().resolve(CORE)
    data = profile.model_dump()
    target = next(r for r in data["resources"] if r["rdf_class"] == str(BF.Instance))
    field = next(f for f in target["fields"] if f["name"] == "instanceOf")
    if change == "minimum":
        field["policy"].update(min_count=0, required=False)
    elif change == "maximum":
        field["policy"].update(max_count=2, repeatable=True)
    else:
        field["value"] = {"kind": "text"}
        field["presentation"]["input"] = "text"
    with pytest.raises(ProfileError) as error:
        resolved(ResolvedProfile.model_validate(data), Graph())
    assert error.value.code == "contradictory_shapes"


def test_violation_prevents_transaction_and_validator_failure_prevents_write() -> None:
    identity = ResourceIdentity.create("https://catalog.invalid/resources")
    graph = rdf(f"<{identity.uri}> a bf:Work.")
    sessions = MagicMock()
    service = BibliographicService(sessions)
    service.store.create = AsyncMock()
    service.store.save = AsyncMock()
    with pytest.raises(ValueError, match="conforme"):
        asyncio.run(
            service.create(
                identity,
                graph,
                Provenance("test", "shacl"),
                profile=INSTITUTIONAL_PROFILE_ID,
                context=TEST_CONTEXT,
            )
        )
    sessions.assert_not_called()
    service.store.save.assert_not_awaited()


def test_shape_cache_isolation_content_invalidation_and_local_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation._parse_shape_terms.cache_clear()
    validation._check_shape_terms.cache_clear()
    names = validation.PROFILE_SHAPES[INSTITUTIONAL_PROFILE_ID]
    first = validation._load_shapes(names)
    second = validation._load_shapes(names)
    assert isomorphic(first, second)
    assert validation._parse_shape_terms.cache_info().hits >= 2
    first.remove((None, None, None))
    assert len(second) > 0
    actual = validation.validate
    calls: list[dict[str, object]] = []

    def execute(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return actual(*args, **kwargs)

    monkeypatch.setattr(validation, "validate", execute)
    graph = rdf('<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "T"].')
    assert validate_profile(graph, INSTITUTIONAL_PROFILE_ID).conforms
    assert validate_profile(graph, INSTITUTIONAL_PROFILE_ID).conforms
    assert sum(bool(call.get("meta_shacl")) for call in calls) == 1
    for call in calls:
        assert call["inference"] == "none"
        for flag in ("advanced", "js", "do_owl_imports", "inplace", "sparql_mode"):
            assert call[flag] is False
    # Content is part of the key: another document never reuses parsed terms.
    assert validation._parse_shape_terms("<urn:a> <urn:p> <urn:b>.") != (
        validation._parse_shape_terms("<urn:a> <urn:p> <urn:c>.")
    )


def test_complex_inverse_path_preserved_in_report() -> None:
    graph = rdf("<urn:i> a bf:Instance; bf:instanceOf <urn:w>.")
    report = validate_profile(graph, INSTITUTIONAL_PROFILE_ID, composed=True)
    complex_issues = [issue for issue in report.issues if isinstance(issue.path_term, BNode)]
    assert complex_issues
    assert any(
        (issue.path_term, SH.inversePath, BF.hasInstance) in report.graph
        for issue in complex_issues
    )
    assert set(graph) == set(rdf("<urn:i> a bf:Instance; bf:instanceOf <urn:w>."))


@pytest.mark.parametrize("change", ["datatype", "target"])
def test_contradictory_datatypes_and_target_classes(change: str) -> None:
    profile = load_profile_registry().resolve(CORE)
    data = profile.model_dump()
    instance = next(r for r in data["resources"] if r["rdf_class"] == str(BF.Instance))
    if change == "datatype":
        publication = next(r for r in data["resources"] if r["rdf_class"] == str(BF.Publication))
        field = next(f for f in publication["fields"] if f["name"] == "date")
        field["value"]["datatypes"] = [str(XSD.integer)]
        field["presentation"]["input"] = "number"
    else:
        field = next(f for f in instance["fields"] if f["name"] == "instanceOf")
        field["value"]["target_classes"] = [str(BF.Person)]
    with pytest.raises(ProfileError) as error:
        resolved(ResolvedProfile.model_validate(data), Graph())
    assert error.value.code == "contradictory_shapes"


def test_engine_failure_cannot_open_create_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = ResourceIdentity.create("https://catalog.invalid/resources")
    graph = rdf(f'<{identity.uri}> a bf:Work; bf:title [a bf:Title; bf:mainTitle "T"].')

    def failure(*args: object, **kwargs: object) -> object:
        raise ShaclValidationError("engine_failure", "motor indisponível")

    monkeypatch.setattr(
        "libris.modules.bibliographic.application.service.validate_profile", failure
    )
    sessions = MagicMock()
    service = BibliographicService(sessions)
    with pytest.raises(ShaclValidationError):
        asyncio.run(
            service.create(
                identity,
                graph,
                Provenance("test", "shacl"),
                profile=INSTITUTIONAL_PROFILE_ID,
                context=TEST_CONTEXT,
            )
        )
    sessions.assert_not_called()


def test_revise_violation_exits_transaction_without_saving() -> None:
    from datetime import UTC, datetime

    from libris.modules.bibliographic.domain.editorial import EditorialRecord
    from libris.modules.bibliographic.domain.models import Revision

    identity = ResourceIdentity.create("https://catalog.invalid/resources")
    graph = rdf(f"<{identity.uri}> a bf:Work.")
    session = MagicMock()
    session.execute = AsyncMock()
    sessions = MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    transaction = session.begin.return_value
    transaction.__aenter__ = AsyncMock()
    transaction.__aexit__ = AsyncMock(return_value=False)
    service = BibliographicService(sessions)
    previous = Revision(
        identity, 1, datetime.now(UTC), Provenance("test", "shacl"), INSTITUTIONAL_PROFILE_ID
    )
    service.store.get = AsyncMock(return_value=(previous, []))
    service.store.advance = AsyncMock(return_value=identity.uri)
    service.store.save = AsyncMock()
    service.editorial.lock_resource = AsyncMock()
    service.editorial.initialize = AsyncMock(return_value=EditorialRecord(identity))
    service.editorial.save = AsyncMock()
    with pytest.raises(ValueError, match="conforme"):
        asyncio.run(
            service.revise(
                identity.internal_id, 1, graph, Provenance("test", "shacl"), context=TEST_CONTEXT
            )
        )
    service.store.advance.assert_awaited_once()
    service.store.save.assert_not_awaited()
    service.editorial.save.assert_not_awaited()
    from libris.modules.bibliographic.application.diagnostics import (
        CatalogValidationRejected,
    )

    assert transaction.__aexit__.await_args.args[0] is CatalogValidationRejected
    assert issubclass(CatalogValidationRejected, ValueError)
    assert transaction.__aexit__.await_args.args[1].result.conforms is False


def test_shape_registry_metadata_and_execution_registry_agree() -> None:
    from importlib.resources import files

    from libris.modules.bibliographic.domain.profile_registry import ShapeRegistry

    metadata = ShapeRegistry.model_validate_json(
        files("libris.modules.bibliographic")
        .joinpath("resources/profiles-v2/shapes-v1.json")
        .read_text()
    )
    assert {
        (entry.id, entry.release, context) for entry in metadata.sets for context in entry.contexts
    } == set(validation.SHAPE_RELEASE_FILES)


def test_multiple_violations_remain_distinct_and_unmapped_results_survive() -> None:
    profile = load_profile_registry().resolve(CORE)
    graph = rdf('<urn:w> a bf:Work; bf:language "pt". <urn:i> a bf:Instance.')
    report = resolved(profile, graph)
    assert not report.conforms
    assert {str(BF.title), str(BF.language), str(BF.instanceOf)} <= {
        issue.path for issue in report.issues
    }
    assert len(correlate_validation_issues(report, profile, graph)) == len(report.issues)


def test_supplied_shapes_path_is_never_opened() -> None:
    with pytest.raises(ShaclValidationError) as error:
        validation._validate_shapes(Graph(), ("https://external.invalid/shapes",))
    assert error.value.code == "unknown_shapes"


def test_optional_language_allowlist_does_not_require_language() -> None:
    profile = load_profile_registry().resolve(CORE)
    data = profile.model_dump()
    title = next(r for r in data["resources"] if r["rdf_class"] == str(BF.Title))
    title["fields"][0]["value"]["language"].update(required=False, allowed=["pt"])
    profile = ResolvedProfile.model_validate(data)
    assert resolved(
        profile, rdf('<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "T"].')
    ).conforms
    assert not resolved(
        profile, rdf('<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "T"@fr].')
    ).conforms


def test_core_supplement_does_not_duplicate_existing_constraints() -> None:
    profile = load_profile_registry().resolve(CORE)
    before = validation._load_shapes(validation.PROFILE_SHAPES[INSTITUTIONAL_PROFILE_ID])
    after = validation._load_shapes(validation.PROFILE_SHAPES[INSTITUTIONAL_PROFILE_ID])
    add_profile_constraints(after, profile)
    assert isomorphic(before, after)


@pytest.mark.parametrize("severity", [SH.Warning, SH.Info])
def test_nonblocking_severity_allows_document_preparation(
    severity: URIRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shapes = Graph()
    rule = URIRef("urn:warning:language")
    shapes.add((rule, RDF.type, SH.PropertyShape))
    shapes.add((rule, SH.targetClass, BF.Work))
    shapes.add((rule, SH.path, BF.language))
    shapes.add((rule, SH.minCount, Literal(1)))
    shapes.add((rule, SH.severity, severity))
    monkeypatch.setattr(validation, "_load_shapes", lambda names: shapes)
    graph = rdf('<urn:w> a bf:Work; bf:title [a bf:Title; bf:mainTitle "T"].')
    assert prepare_document(graph, "urn:w", INSTITUTIONAL_PROFILE_ID)
