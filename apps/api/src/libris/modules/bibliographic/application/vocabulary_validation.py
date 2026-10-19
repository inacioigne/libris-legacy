"""Supplementary operational checks; no claim of SHACL or external verification."""

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
from libris.modules.bibliographic.application.diagnostics import Diagnostic
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
    StructuredValue,
    TextValue,
    TypedValue,
)
from libris.modules.bibliographic.domain.vocabularies import VocabularyRegistry


def reference_status_diagnostics(
    status: ReferenceStatus,
    *,
    field_id: str | None = None,
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
                else "Pertença ao vocabulário ainda não verificada."
            ),
            category="reference",
            severity="Violation" if membership is False else "Warning",
            field_ids=(field_id,) if field_id is not None else (),
        ),
    )


def validate_catalog_value(
    value: CatalogValue,
    field: DeclarativeField,
    *,
    vocabularies: VocabularyRegistry = VocabularyRegistry(),
    field_id: str | None = None,
) -> tuple[Diagnostic, ...]:
    """Use fields from a trusted ResolvedProfile; declared vocabulary is not evidence."""
    issues: list[Diagnostic] = []

    def add(code: CatalogCode, message: str, *, warning: bool = False) -> None:
        issues.append(
            Diagnostic(
                code=code,
                message=message,
                category="reference",
                severity="Warning" if warning else "Violation",
                field_ids=(field_id or field.name,),
            )
        )

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
        compatible = False  # Structured auxiliaries are not scalar CatalogValue instances.
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
        issues.extend(reference_status_diagnostics(value.status, field_id=field_id))
        if isinstance(definition, ReferenceValue) and definition.vocabulary is not None:
            membership = vocabularies.membership(value.uri, definition.vocabulary)
            issues.extend(vocabulary_membership_diagnostics(membership, field_id=field_id))
    elif isinstance(value, EntityValue) and isinstance(value.reference, UnreconciledReference):
        add(CatalogCode.REFERENCE_UNVERIFIED, "Entidade ainda não reconciliada.", warning=True)
    return tuple(issues)


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
