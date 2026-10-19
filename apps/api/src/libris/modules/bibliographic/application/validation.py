"""Prevalidation uses the existing engine; technical failures never claim conformity."""

import logging
from typing import Literal

from rdflib import Graph

from libris.core.correlation import request_id_context
from libris.modules.bibliographic.application.diagnostics import (
    CatalogValidationResult,
    Diagnostic,
    ordered,
)
from libris.modules.bibliographic.application.vocabulary_validation import (
    validate_profile_vocabulary_membership,
)
from libris.modules.bibliographic.domain.errors import CatalogCode
from libris.modules.bibliographic.domain.profile_contract import (
    ProfileValidationContext,
    ResolvedProfile,
)
from libris.modules.bibliographic.domain.profile_registry import ProfileError
from libris.modules.bibliographic.domain.profiles import CatalogProfile
from libris.modules.bibliographic.domain.vocabularies import VocabularyRegistry
from libris.modules.bibliographic.infrastructure.diagnostics import adapt_report
from libris.modules.bibliographic.infrastructure.profiles import (
    LEGACY_PROFILES,
    load_legacy_profile,
)
from libris.modules.bibliographic.infrastructure.validation import (
    ShaclValidationError,
    validate_profile,
    validate_resolved_profile,
)

logger = logging.getLogger(__name__)


def validate_catalog(
    graph: Graph,
    profile: str | ResolvedProfile,
    *,
    context: Literal["snapshot", "composed"] = "snapshot",
    request_id: str | None = None,
    vocabularies: VocabularyRegistry | None = None,
    operation: Literal["prevalidation", "create", "update", "restore", "publish"] = (
        "prevalidation"
    ),
    previous_graph: Graph | None = None,
) -> CatalogValidationResult:
    """ResolvedProfile must come from the trusted registry, never a request body."""
    descriptor: CatalogProfile | ResolvedProfile
    if isinstance(profile, str):
        if profile not in LEGACY_PROFILES:
            raise ProfileError("unknown_profile", "Perfil local desconhecido.")
        try:
            descriptor = load_legacy_profile(profile)
        except OSError as error:
            raise ProfileError("profile_unavailable", "Perfil local indisponível.") from error
    else:
        descriptor = profile
    correlation = request_id_context.get() or request_id
    try:
        report = (
            validate_profile(graph, profile, composed=context == "composed")
            if isinstance(profile, str)
            else validate_resolved_profile(
                graph,
                profile,
                ProfileValidationContext(profile_release=profile.release_id, mode=context),
            )
        )
    except ShaclValidationError as error:
        logger.error(
            "request_id=%s validator_code=%s exception_type=%s",
            correlation,
            error.code,
            type(error).__name__,
        )
        code = (
            CatalogCode.INFRASTRUCTURE_UNAVAILABLE
            if error.code == "shapes_unavailable"
            else CatalogCode.VALIDATOR_FAILURE
        )
        return CatalogValidationResult(
            state="failed",
            profile_id=descriptor.id,
            profile_release=profile if isinstance(profile, str) else profile.release_id,
            profile_version=descriptor.release,
            context=context,
            request_id=correlation,
            issues=(
                Diagnostic(code=code, message="Validação indisponível.", category="technical"),
            ),
        )
    result = adapt_report(report, descriptor, graph, context=context, request_id=correlation)
    if vocabularies is None:
        return result
    membership_issues = validate_profile_vocabulary_membership(
        graph,
        descriptor,
        vocabularies,
        operation=operation,
        previous_graph=previous_graph,
    )
    if not membership_issues:
        return result
    issues = ordered((*result.issues, *membership_issues))
    return result.model_copy(
        update={
            "conforms": result.conforms is True
            and not any(issue.severity == "Violation" for issue in membership_issues),
            "issues": issues,
        }
    )
