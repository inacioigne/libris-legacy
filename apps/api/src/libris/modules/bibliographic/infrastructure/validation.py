from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Literal, TypedDict

from pyshacl import validate
from pyshacl.errors import ConstraintLoadError, ReportableRuntimeError, ShapeLoadError
from rdflib import Graph, URIRef
from rdflib.namespace import RDF, SH
from rdflib.term import Node

from libris.modules.bibliographic.domain.profile_contract import (
    ProfileValidationContext,
    ResolvedProfile,
)
from libris.modules.bibliographic.domain.profile_registry import ProfileError
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
)
from libris.modules.bibliographic.infrastructure.rdf import check_graph

PROFILE_ID = "urn:libris:profile:monograph:v1"
INSTITUTIONAL_PROFILE_ID = "urn:libris:profile:monograph:v1:1"
AUTHORITY_PROFILE_ID = "urn:libris:profile:authority:v1"
# Compatibility aliases resolve through the same package-owned release registry.
LEGACY_SHAPE_RELEASES = {
    PROFILE_ID: ("urn:libris:shapes:experimental", "1.0"),
    INSTITUTIONAL_PROFILE_ID: ("urn:libris:shapes:institutional", "1.1"),
    CONTROLLED_VOCABULARY_PROFILE_ID: ("urn:libris:shapes:institutional", "1.1"),
    AUTHORITY_PROFILE_ID: ("urn:libris:shapes:institutional", "1.1"),
}
SHAPE_RELEASE_FILES = {
    ("urn:libris:shapes:institutional", "1.1", "snapshot"): (
        "monograph-v1.1.ttl",
        "authority-v1.ttl",
    ),
    ("urn:libris:shapes:institutional", "1.1", "composed"): (
        "monograph-v1.1.ttl",
        "authority-v1.ttl",
        "monograph-v1.1-composed.ttl",
    ),
    ("urn:libris:shapes:experimental", "1.0", "snapshot"): ("monograph-v1.ttl",),
}
PROFILE_SHAPES = {
    profile: SHAPE_RELEASE_FILES[(*release, "snapshot")]
    for profile, release in LEGACY_SHAPE_RELEASES.items()
}


class _ValidationOptions(TypedDict):
    inference: Literal["none"]
    advanced: bool
    js: bool
    do_owl_imports: bool
    allow_warnings: bool
    allow_infos: bool
    inplace: bool
    sparql_mode: bool


class ShaclValidationError(ValueError):
    """Infrastructure failure, never a cataloging violation or an HTTP contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidationIssue:
    focus_node: str
    path: str | None
    severity: str
    messages: tuple[str, ...]
    source_shape: str | None = None
    source_constraint_component: str | None = None
    value: str | None = None
    # Exact terms are internal and scoped to report.graph, not public API types.
    focus_term: Node | None = None
    path_term: Node | None = None
    source_shape_term: Node | None = None
    value_term: Node | None = None
    message_terms: tuple[Node, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    conforms: bool
    issues: tuple[ValidationIssue, ...]
    graph: Graph


def validate_monograph(graph: Graph) -> ValidationReport:
    return validate_profile(graph, PROFILE_ID)


def validate_profile(graph: Graph, profile: str, *, composed: bool = False) -> ValidationReport:
    check_graph(graph)
    try:
        release = LEGACY_SHAPE_RELEASES[profile]
    except KeyError as error:
        raise ProfileError("unknown_profile", "Perfil local desconhecido.") from error
    # Experimental v1 already checks inverses in its sole historical artifact.
    mode = "composed" if composed and profile != PROFILE_ID else "snapshot"
    return _validate_shapes(graph, SHAPE_RELEASE_FILES[(*release, mode)])


def _load_shapes(names: tuple[str, ...]) -> Graph:
    shapes = Graph()
    allowed = {name for names in SHAPE_RELEASE_FILES.values() for name in names}
    for name in names:
        if name not in allowed:
            raise ShaclValidationError("unknown_shapes", "Artefato SHACL fora do registro local.")
        try:
            document = (
                files("libris.modules.bibliographic")
                .joinpath(f"resources/{name}")
                .read_text(encoding="utf-8")
            )
        except OSError as error:
            raise ShaclValidationError(
                "shapes_unavailable", "Artefato SHACL indisponível."
            ) from error
        try:
            # Separate parsers keep blank nodes from different artifacts disjoint.
            for triple in _parse_shape_terms(document):
                shapes.add(triple)
        except Exception as error:
            raise ShaclValidationError("malformed_shapes", "Turtle SHACL malformado.") from error
    return shapes


@lru_cache(maxsize=16)
def _parse_shape_terms(document: str) -> tuple[tuple[Node, Node, Node], ...]:
    """Cache immutable terms, never a shared mutable Graph; content pins each entry."""
    return tuple(Graph().parse(data=document, format="turtle"))


def _execution_options() -> _ValidationOptions:
    return dict(
        inference="none",
        advanced=False,
        js=False,
        do_owl_imports=False,
        allow_warnings=True,
        allow_infos=True,
        inplace=False,
        sparql_mode=False,
    )


@lru_cache(maxsize=16)
def _check_shape_terms(terms: frozenset[tuple[Node, Node, Node]]) -> None:
    """Meta-SHACL uses bundled rules. Cache successful checks of immutable content."""
    shapes = Graph()
    for triple in terms:
        shapes.add(triple)
    try:
        valid, report, _ = validate(
            Graph(), shacl_graph=shapes, meta_shacl=True, **_execution_options()
        )
    except (ShapeLoadError, ConstraintLoadError) as error:
        raise ShaclValidationError("malformed_shapes", "Shapes SHACL inválidas.") from error
    except ReportableRuntimeError as error:
        code = (
            "malformed_shapes" if "SHACL File does not validate" in str(error) else "engine_failure"
        )
        raise ShaclValidationError(code, "Falha na verificação das shapes SHACL.") from error
    except Exception as error:
        raise ShaclValidationError(
            "engine_failure", "Falha interna da verificação SHACL."
        ) from error
    if not valid or not isinstance(report, Graph):
        raise ShaclValidationError("malformed_shapes", "Shapes SHACL inválidas.")


def _validate_shapes(
    graph: Graph, names: tuple[str, ...], *, profile: ResolvedProfile | None = None
) -> ValidationReport:
    shapes = _load_shapes(names)
    _check_shape_terms(frozenset(shapes))
    if profile is not None:
        from .validation_policy import add_profile_constraints

        add_profile_constraints(shapes, profile)
    options = _execution_options()
    try:
        conforms, report, _ = validate(graph, shacl_graph=shapes, **options)
    except (ShapeLoadError, ConstraintLoadError) as error:
        raise ShaclValidationError("malformed_shapes", "Constraints SHACL inválidas.") from error
    except Exception as error:
        raise ShaclValidationError("engine_failure", "Falha interna da validação SHACL.") from error
    if not isinstance(report, Graph):
        raise ShaclValidationError("engine_failure", "Falha na execução da validação SHACL.")
    issues = []
    for result in report.subjects(RDF.type, SH.ValidationResult):
        focus = report.value(result, SH.focusNode)
        path = report.value(result, SH.resultPath)
        source = report.value(result, SH.sourceShape)
        component = report.value(result, SH.sourceConstraintComponent)
        value = report.value(result, SH.value)
        messages = tuple(report.objects(result, SH.resultMessage))
        issues.append(
            ValidationIssue(
                focus_node=str(focus),
                path=str(path) if path is not None else None,
                severity=str(report.value(result, SH.resultSeverity)),
                messages=tuple(str(message) for message in messages),
                source_shape=str(source) if source is not None else None,
                source_constraint_component=str(component) if component is not None else None,
                value=str(value) if value is not None else None,
                focus_term=focus,
                path_term=path,
                source_shape_term=source,
                value_term=value,
                message_terms=messages,
            )
        )
    return ValidationReport(bool(conforms), tuple(issues), report)


@dataclass(frozen=True)
class FieldValidationIssue:
    issue: ValidationIssue
    field_ids: tuple[str, ...]


def validate_resolved_profile(
    graph: Graph, profile: ResolvedProfile, context: ProfileValidationContext
) -> ValidationReport:
    """Contract adapter only; all RDF validation still runs through existing pySHACL."""
    check_graph(graph)
    if context.profile_release != profile.release_id:
        raise ProfileError(
            "validation_release_mismatch", "Contexto e perfil usam releases distintas."
        )
    names: list[str] = []
    for reference in profile.validation:
        if (reference.id, reference.release, reference.context) not in SHAPE_RELEASE_FILES:
            raise ProfileError("unknown_shapes", "Conjunto/contexto SHACL local não suportado.")
        # A composed run applies snapshot rules too and adds local inverse checks.
        if context.mode == "snapshot" and reference.context != "snapshot":
            continue
        # A composed-only reference cannot be promoted to snapshot rules.
        key = (reference.id, reference.release, context.mode)
        try:
            names.extend(SHAPE_RELEASE_FILES[key])
        except KeyError as error:
            raise ProfileError(
                "unknown_shapes", "Conjunto/contexto SHACL local não suportado."
            ) from error
    if not names:
        raise ProfileError("missing_shapes", "Nenhum conjunto SHACL aplicável ao contexto.")
    return _validate_shapes(graph, tuple(dict.fromkeys(names)), profile=profile)


def correlate_validation_issues(
    report: ValidationReport, profile: ResolvedProfile, graph: Graph
) -> tuple[FieldValidationIssue, ...]:
    """Unmapped/complex paths keep the original issue and an empty field list."""
    mapped: list[FieldValidationIssue] = []
    for issue in report.issues:
        if issue.path_term is not None and not isinstance(issue.path_term, URIRef):
            mapped.append(FieldValidationIssue(issue, ()))
            continue
        if issue.focus_term is not None:
            nodes = [issue.focus_term]
        else:
            # Legacy manually constructed reports lack term identity. Avoid collisions.
            nodes = [node for node in graph.all_nodes() if str(node) == issue.focus_node]
            if len(nodes) != 1:
                mapped.append(FieldValidationIssue(issue, ()))
                continue
        classes = {str(kind) for node in nodes for kind in graph.objects(node, RDF.type)}
        fields = tuple(
            sorted(
                {
                    field
                    for rdf_class in classes
                    for field in profile.fields_for_path(issue.path, rdf_class=rdf_class)
                }
            )
        )
        mapped.append(FieldValidationIssue(issue, fields))
    return tuple(mapped)
