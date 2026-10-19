"""Supplementary operational checks; no claim of SHACL or external verification."""

from typing import Literal

from rdflib import Graph, URIRef
from rdflib.namespace import RDF
from sqlalchemy.ext.asyncio import AsyncSession

from libris.modules.bibliographic.api.schemas.common import UnreconciledReference
from libris.modules.bibliographic.application.controlled_values import (
    STRING_DATATYPE,
    CatalogValue,
    ControlledConcept,
    EntityValue,
    LiteralValue,
    LocalConcept,
    ReferenceStatus,
)
from libris.modules.bibliographic.application.diagnostics import Diagnostic, RdfPath
from libris.modules.bibliographic.application.vocabulary_resolution import (
    ResolutionFailure,
    ResolutionResult,
)
from libris.modules.bibliographic.domain.errors import CatalogCode
from libris.modules.bibliographic.domain.profile_contract import (
    DeclarativeField,
    IRIDefault,
    LiteralDefault,
    ReferenceValue,
    ResolvedProfile,
    StructuredValue,
    TextValue,
    TypedValue,
)
from libris.modules.bibliographic.domain.profiles import CatalogProfile, ProfileField
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
    VocabularyConceptEvidence,
    VocabularyDefinition,
    VocabularyRegistry,
)
from libris.modules.bibliographic.infrastructure.validation import INSTITUTIONAL_PROFILE_ID
from libris.modules.bibliographic.infrastructure.vocabulary_catalog import (
    VocabularyCatalogRepository,
)

ProfileVocabularyField = ProfileField | DeclarativeField


def reference_status_diagnostics(
    status: ReferenceStatus,
    *,
    field_id: str | None = None,
    path: RdfPath | None = None,
    value: str | None = None,
    replacement: str | None = None,
) -> tuple[Diagnostic, ...]:
    issues: list[Diagnostic] = []

    def add(code: CatalogCode, message: str, *, warning: bool = False) -> None:
        issues.append(
            Diagnostic(
                code=code,
                message=message,
                category="reference",
                severity="Warning" if warning else "Violation",
                field_ids=(field_id,) if field_id is not None else (),
                path=path,
                value=value,
                parameters={"replacement_uri": replacement} if replacement else {},
            )
        )

    if status == "invalid":
        add(CatalogCode.REFERENCE_INVALID, "Conceito reconhecido como inválido.")
    elif status == "deprecated":
        add(
            CatalogCode.REFERENCE_DEPRECATED,
            "Conceito descontinuado; revisar uso.",
            warning=True,
        )
    elif status in {"unverified", "unavailable"}:
        add(
            CatalogCode.REFERENCE_UNVERIFIED,
            "Identidade do conceito ainda não verificada.",
            warning=True,
        )
        if status == "unavailable":
            add(
                CatalogCode.RESOLVER_UNAVAILABLE,
                "Fonte temporariamente indisponível.",
                warning=True,
            )
    return tuple(issues)


def vocabulary_membership_diagnostics(
    membership: bool | None,
    *,
    field_id: str | None = None,
    path: RdfPath | None = None,
    value: str | None = None,
    require_membership: bool = False,
    release: str | None = None,
) -> tuple[Diagnostic, ...]:
    if membership is True:
        return ()
    return (
        Diagnostic(
            code=(
                CatalogCode.VOCABULARY_MISMATCH
                if membership is False
                else CatalogCode.VOCABULARY_UNVERIFIED
            ),
            message=(
                "Conceito fora do vocabulário obrigatório."
                if membership is False
                else (
                    "Publicação bloqueada: a pertença ao vocabulário não foi comprovada."
                    if require_membership
                    else "Pertença ao vocabulário ainda não verificada."
                )
            ),
            category="reference",
            severity=(
                "Violation"
                if membership is False or require_membership
                else "Warning"
            ),
            field_ids=(field_id,) if field_id is not None else (),
            path=path,
            value=value,
            parameters={"vocabulary_release": release} if release else {},
        ),
    )


def validate_catalog_value(
    value: CatalogValue,
    field: DeclarativeField | ProfileField,
    *,
    vocabularies: VocabularyRegistry = VocabularyRegistry(),
    field_id: str | None = None,
    require_membership: bool = False,
    path: RdfPath | None = None,
    rdf_value: str | None = None,
) -> tuple[Diagnostic, ...]:
    """Use trusted profile fields; declarations are policy, not membership evidence."""
    issues: list[Diagnostic] = []

    def add(code: CatalogCode, message: str, *, warning: bool = False) -> None:
        issues.append(
            Diagnostic(
                code=code,
                message=message,
                category="reference",
                severity="Warning" if warning else "Violation",
                field_ids=(field_id or field.name,),
                path=path,
                value=rdf_value,
            )
        )

    if isinstance(field, ProfileField):
        if field.value_kind != "resource" or not isinstance(
            value, (ControlledConcept, LocalConcept)
        ):
            add(CatalogCode.VALUE, "Tipo de valor incompatível com o campo.")
            return tuple(issues)
        profile_membership = (
            vocabularies.membership(value.uri, field.vocabulary)
            if field.vocabulary is not None
            else None
        )
        if not (
            profile_membership is False and value.status in {"unverified", "unavailable"}
        ):
            issues.extend(
                reference_status_diagnostics(
                    value.status,
                    field_id=field_id or field.name,
                    path=path,
                    value=rdf_value or value.uri,
                    replacement=value.replacement,
                )
            )
        if field.vocabulary is not None and value.status != "invalid":
            vocabulary_definition = vocabularies.definition(field.vocabulary)
            issues.extend(
                vocabulary_membership_diagnostics(
                    profile_membership,
                    field_id=field_id or field.name,
                    path=path,
                    value=rdf_value or value.uri,
                    require_membership=require_membership,
                    release=(
                        vocabulary_definition.release if vocabulary_definition else None
                    ),
                )
            )
        return tuple(issues)

    definition = field.value
    compatible = True
    if isinstance(definition, (TextValue, TypedValue)):
        compatible = isinstance(value, LiteralValue)
        if isinstance(value, LiteralValue):
            try:
                field.check_value(
                    LiteralDefault(
                        kind="literal",
                        value=value.value,
                        language=value.language,
                        datatype=value.datatype,
                    )
                )
            except ValueError:
                compatible = False
            if isinstance(definition, TextValue) and value.datatype not in {None, STRING_DATATYPE}:
                compatible = False
    elif isinstance(definition, StructuredValue):
        compatible = False
    elif isinstance(definition, ReferenceValue):
        compatible = isinstance(value, (ControlledConcept, LocalConcept, EntityValue))
        if definition.kind == "vocabulary":
            compatible = isinstance(value, (ControlledConcept, LocalConcept))
        elif definition.kind in {"entity", "authority", "relationship"}:
            compatible = isinstance(value, EntityValue)
            if isinstance(value, EntityValue):
                compatible = value.reference.entity_type in definition.target_entities
    if not compatible:
        add(CatalogCode.VALUE, "Tipo de valor incompatível com o campo.")
        return tuple(issues)

    if field.policy.allowed_values:
        if isinstance(value, LiteralValue):
            allowed = (
                LiteralDefault(
                    kind="literal",
                    value=value.value,
                    language=value.language,
                    datatype=value.datatype,
                )
                in field.policy.allowed_values
            )
        else:
            uri = (
                value.uri
                if isinstance(value, (ControlledConcept, LocalConcept))
                else value.reference.uri
                if isinstance(value, EntityValue)
                and not isinstance(value.reference, UnreconciledReference)
                else None
            )
            allowed = uri is not None and IRIDefault(kind="iri", iri=uri) in (
                field.policy.allowed_values
            )
        if not allowed:
            add(CatalogCode.PROFILE_POLICY, "Valor fora dos valores permitidos pelo perfil.")

    if isinstance(value, (ControlledConcept, LocalConcept)):
        catalog_membership: bool | None = None
        if isinstance(definition, ReferenceValue) and definition.vocabulary is not None:
            catalog_membership = vocabularies.membership(value.uri, definition.vocabulary)
            vocabulary = vocabularies.definition(definition.vocabulary)
        if not (
            catalog_membership is False and value.status in {"unverified", "unavailable"}
        ):
            issues.extend(
                reference_status_diagnostics(
                    value.status,
                    field_id=field_id,
                    path=path,
                    value=rdf_value or value.uri,
                    replacement=value.replacement,
                )
            )
        if isinstance(definition, ReferenceValue) and definition.vocabulary is not None:
            if value.status != "invalid":
                issues.extend(
                    vocabulary_membership_diagnostics(
                        catalog_membership,
                        field_id=field_id,
                        path=path,
                        value=rdf_value or value.uri,
                        require_membership=require_membership,
                        release=vocabulary.release if vocabulary else None,
                    )
                )
    elif isinstance(value, EntityValue) and isinstance(value.reference, UnreconciledReference):
        add(CatalogCode.REFERENCE_UNVERIFIED, "Entidade ainda não reconciliada.", warning=True)
    return tuple(issues)


def profile_uses_membership_policy(
    profile: str | CatalogProfile | ResolvedProfile,
    operation: Literal["prevalidation", "create", "update", "restore", "publish"],
) -> bool:
    profile_id = profile if isinstance(profile, str) else profile.id
    return profile_id == CONTROLLED_VOCABULARY_PROFILE_ID or (
        profile_id == INSTITUTIONAL_PROFILE_ID and operation == "update"
    )


async def vocabulary_registry_for_profile(
    session: AsyncSession,
    profile: str | CatalogProfile | ResolvedProfile,
    *,
    operation: Literal["prevalidation", "create", "update", "restore", "publish"],
) -> VocabularyRegistry:
    """Project only profile-declared vocabularies from persisted active releases."""
    if not profile_uses_membership_policy(profile, operation):
        return VocabularyRegistry()
    if isinstance(profile, str):
        from libris.modules.bibliographic.infrastructure.profiles import load_legacy_profile

        descriptor: CatalogProfile | ResolvedProfile = load_legacy_profile(profile)
    else:
        descriptor = profile

    vocabulary_uris: set[str] = set()
    for resource in descriptor.resources:
        for field in resource.fields:
            vocabulary = (
                field.vocabulary
                if isinstance(field, ProfileField)
                else field.value.vocabulary
                if isinstance(field.value, ReferenceValue)
                and field.value.kind == "vocabulary"
                else None
            )
            if vocabulary is not None:
                vocabulary_uris.add(vocabulary)

    snapshots = await VocabularyCatalogRepository().membership_snapshots(
        session, tuple(sorted(vocabulary_uris))
    )
    definitions: list[VocabularyDefinition] = []
    for snapshot in snapshots:
        if snapshot.completeness not in {"complete", "partial"}:
            raise RuntimeError("Persisted vocabulary release has invalid completeness.")
        concepts = tuple(
            VocabularyConceptEvidence(
                uri=uri,
                status=status,
                source=(
                    source or snapshot.source_uri
                    if status in {"resolved", "invalid", "deprecated"}
                    else source
                ),
                replacement=replacement,
            )
            for uri, status, source, replacement in snapshot.concepts
        )
        definitions.append(
            VocabularyDefinition(
                uri=snapshot.vocabulary_uri,
                release=snapshot.release_key,
                source=snapshot.source_uri,
                members=tuple(
                    concept.uri
                    for concept in concepts
                    if concept.status in {"resolved", "deprecated"}
                ),
                concepts=concepts,
                complete=snapshot.completeness == "complete",
            )
        )
    return VocabularyRegistry(vocabularies=tuple(definitions))


def _profile_vocabulary_fields(
    profile: CatalogProfile | ResolvedProfile,
) -> tuple[tuple[str, str, str, str, ProfileVocabularyField], ...]:
    fields: list[tuple[str, str, str, str, ProfileVocabularyField]] = []
    for resource in profile.resources:
        for field in resource.fields:
            if isinstance(field, ProfileField):
                vocabulary = field.vocabulary
            elif isinstance(field.value, ReferenceValue) and field.value.kind == "vocabulary":
                vocabulary = field.value.vocabulary
            else:
                vocabulary = None
            if vocabulary is not None:
                fields.append((resource.rdf_class, field.name, field.property, vocabulary, field))
    return tuple(fields)


def validate_profile_vocabulary_membership(
    graph: Graph,
    profile: CatalogProfile | ResolvedProfile,
    vocabularies: VocabularyRegistry,
    *,
    operation: Literal["prevalidation", "create", "update", "restore", "publish"],
    previous_graph: Graph | None = None,
) -> tuple[Diagnostic, ...]:
    """Validate values declared by the pinned profile, without inferring membership."""
    if not profile_uses_membership_policy(profile, operation):
        return ()
    require_membership = False
    diagnostics: list[Diagnostic] = []
    for rdf_class, name, predicate, vocabulary_uri, field in _profile_vocabulary_fields(profile):
        class_node = URIRef(rdf_class)
        property_node = URIRef(predicate)
        for subject in graph.subjects(RDF.type, class_node):
            current_values = set(graph.objects(subject, property_node))
            if previous_graph is not None:
                previous_values = (
                    set(previous_graph.objects(subject, property_node))
                    if isinstance(subject, URIRef)
                    else {
                        value
                        for prior_subject in previous_graph.subjects(RDF.type, class_node)
                        for value in previous_graph.objects(prior_subject, property_node)
                    }
                )
                current_values.difference_update(previous_values)
            for node in sorted(current_values, key=str):
                if not isinstance(node, URIRef):
                    continue
                uri = str(node)
                evidence = vocabularies.concept_evidence(uri, vocabulary_uri)
                vocabulary = vocabularies.definition(vocabulary_uri)
                membership = vocabularies.membership(uri, vocabulary_uri)
                field_id = f"/{name}"
                path = RdfPath(kind="iri", iri=predicate)
                concept = ControlledConcept(
                    kind="controlled_concept",
                    uri=uri,
                    vocabulary=vocabulary_uri,
                    status=(
                        evidence.status
                        if evidence
                        else "resolved"
                        if membership is True
                        else "unverified"
                    ),
                    source=(
                        evidence.source
                        if evidence
                        else vocabulary.source
                        if membership is True and vocabulary is not None
                        else None
                    ),
                    replacement=evidence.replacement if evidence else None,
                )
                diagnostics.extend(
                    validate_catalog_value(
                        concept,
                        field,
                        vocabularies=vocabularies,
                        field_id=field_id,
                        require_membership=require_membership,
                        path=path,
                        rdf_value=uri,
                    )
                )
    return tuple(diagnostics)


def resolution_diagnostics(result: ResolutionResult) -> tuple[Diagnostic, ...]:
    """Attempt failures are warnings; even not_found requires scoped evidence review."""
    if not isinstance(result, ResolutionFailure):
        return ()
    transient = result.outcome == "temporary_failure"
    return (
        Diagnostic(
            code=CatalogCode.RESOLVER_UNAVAILABLE
            if transient
            else CatalogCode.REFERENCE_UNVERIFIED,
            message="Fonte temporariamente indisponível."
            if transient
            else "Consulta sem confirmação da identidade; revisar evidência da fonte.",
            category="reference",
            severity="Warning",
            parameters={"reason": result.reason},
        ),
    )
