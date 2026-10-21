"""Immutable editorial policy. No RDF, HTTP, actors, ORM or durable event claims."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from .errors import CatalogCode as C
from .errors import CatalogError
from .models import ResourceIdentity, Revision


class EditorialState(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


class EditorialOperation(StrEnum):
    SUBMIT = "submit"
    APPROVE = "approve"
    RETURN = "return"
    PUBLISH = "publish"
    WITHDRAW = "withdraw"
    REPUBLISH = "republish"
    ARCHIVE = "archive"


class EditorialScope(StrEnum):
    RECORD = "record"
    CANDIDATE = "candidate"


S = EditorialState
Op = EditorialOperation
TRANSITIONS: Mapping[tuple[S, S], Op] = MappingProxyType(
    {
        (S.DRAFT, S.IN_REVIEW): Op.SUBMIT,
        (S.IN_REVIEW, S.APPROVED): Op.APPROVE,
        (S.IN_REVIEW, S.DRAFT): Op.RETURN,
        (S.APPROVED, S.PUBLISHED): Op.PUBLISH,
        (S.APPROVED, S.DRAFT): Op.RETURN,
        (S.PUBLISHED, S.WITHDRAWN): Op.WITHDRAW,
        (S.WITHDRAWN, S.PUBLISHED): Op.REPUBLISH,
        (S.WITHDRAWN, S.ARCHIVED): Op.ARCHIVE,
    }
)
JUSTIFIED = frozenset({Op.RETURN, Op.WITHDRAW, Op.ARCHIVE})


def state_value(value: str) -> S:
    try:
        return S(value)
    except (ValueError, TypeError) as error:
        raise CatalogError(C.EDITORIAL_STATE_INVALID, "Estado editorial desconhecido.") from error


def transition_operation(source: str, target: str) -> Op:
    pair = (state_value(source), state_value(target))
    if pair not in TRANSITIONS:
        raise CatalogError(C.TRANSITION_FORBIDDEN, "Transição editorial não permitida.")
    return TRANSITIONS[pair]


@dataclass(frozen=True)
class EditorialRecord:
    identity: ResourceIdentity
    current_revision: int = 1
    editorial_state: S = S.DRAFT
    version: int = 1
    approved_revision: int | None = None
    published_revision: int | None = None
    candidate_state: S | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "editorial_state", state_value(self.editorial_state))
        if self.candidate_state is not None:
            object.__setattr__(self, "candidate_state", state_value(self.candidate_state))
        if any(type(n) is not int or n < 1 for n in (self.current_revision, self.version)):
            raise CatalogError(C.EDITORIAL_PRECONDITION, "Revisão e versão devem ser positivas.")
        for number in (self.approved_revision, self.published_revision):
            if number is not None and (
                type(number) is not int or not 1 <= number <= self.current_revision
            ):
                raise CatalogError(C.EDITORIAL_PRECONDITION, "Ponteiro de revisão inválido.")
        if self.editorial_state in {S.PUBLISHED, S.WITHDRAWN, S.ARCHIVED}:
            if self.published_revision is None:
                raise CatalogError(C.EDITORIAL_PRECONDITION, "Histórico de publicação obrigatório.")
        elif self.published_revision is not None:
            raise CatalogError(C.EDITORIAL_PRECONDITION, "Estado público incompatível.")
        if self.candidate_state is not None:
            if self.editorial_state not in {S.PUBLISHED, S.WITHDRAWN} or (
                self.candidate_state not in {S.DRAFT, S.IN_REVIEW, S.APPROVED}
                or self.published_revision == self.current_revision
            ):
                raise CatalogError(
                    C.EDITORIAL_PRECONDITION, "Candidata incompatível com publicação."
                )
        elif self.editorial_state in {S.PUBLISHED, S.WITHDRAWN} and (
            self.current_revision != self.published_revision
        ):
            raise CatalogError(C.EDITORIAL_PRECONDITION, "Alteração pendente exige candidata.")
        workflow = self.candidate_state or self.editorial_state
        if workflow == S.APPROVED and self.approved_revision != self.current_revision:
            raise CatalogError(C.APPROVAL_STALE, "Aprovação deve apontar para a revisão corrente.")
        if workflow in {S.DRAFT, S.IN_REVIEW} and self.approved_revision is not None:
            raise CatalogError(C.APPROVAL_STALE, "Conteúdo em elaboração não possui aprovação.")
        if self.candidate_state is None and self.editorial_state in {S.PUBLISHED, S.WITHDRAWN}:
            if self.approved_revision != self.published_revision:
                raise CatalogError(C.APPROVAL_STALE, "Publicação deve conservar sua aprovação.")

    @property
    def candidate_revision(self) -> int | None:
        return self.current_revision if self.candidate_state is not None else None

    @property
    def public_revision(self) -> int | None:
        return self.published_revision if self.editorial_state == S.PUBLISHED else None

    def require_expected(self, version: int, current_revision: int) -> None:
        if version != self.version:
            raise CatalogError(C.EDITORIAL_VERSION_STALE, "Versão editorial obsoleta.")
        if current_revision != self.current_revision:
            raise CatalogError(C.REVISION_STALE, "Revisão de conteúdo obsoleta.")

    def source(self, scope: EditorialScope) -> S:
        if scope == EditorialScope.CANDIDATE:
            if self.candidate_state is None:
                raise CatalogError(C.EDITORIAL_PRECONDITION, "Não existe revisão candidata.")
            return self.candidate_state
        if scope != EditorialScope.RECORD:
            raise CatalogError(C.EDITORIAL_PRECONDITION, "Escopo editorial desconhecido.")
        return self.editorial_state


@dataclass(frozen=True)
class PublicationReadiness:
    """Evidence prepared by the application after catalog and reference validation."""

    identity: ResourceIdentity
    revision: int
    profile: str


@dataclass(frozen=True)
class PreparedEditorialEvent:
    identity: ResourceIdentity
    previous_state: S
    next_state: S
    operation: Op
    scope: EditorialScope
    actor_id: str
    occurred_at: datetime
    revision: int
    previous_version: int
    next_version: int
    justification: str | None


@dataclass(frozen=True)
class PreparedTransition:
    before: EditorialRecord
    after: EditorialRecord
    event: PreparedEditorialEvent


def prepare_transition(
    record: EditorialRecord,
    target: str,
    *,
    expected_version: int,
    expected_current_revision: int,
    selected_revision: int,
    actor_id: str,
    occurred_at: datetime,
    scope: EditorialScope = EditorialScope.RECORD,
    justification: str | None = None,
    readiness: PublicationReadiness | None = None,
) -> PreparedTransition:
    record.require_expected(expected_version, expected_current_revision)
    source = record.source(scope)
    destination = state_value(target)
    operation = transition_operation(source, destination)
    if not actor_id.strip() or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise CatalogError(C.EDITORIAL_PRECONDITION, "Ator e instante com timezone obrigatórios.")
    if operation in JUSTIFIED and (justification is None or not justification.strip()):
        raise CatalogError(C.EDITORIAL_PRECONDITION, "Justificativa obrigatória.")
    if (
        scope == EditorialScope.RECORD
        and record.candidate_state is not None
        and operation
        not in {
            Op.WITHDRAW,
            Op.REPUBLISH,
            Op.ARCHIVE,
        }
    ):
        raise CatalogError(C.TRANSITION_FORBIDDEN, "Use o ciclo da revisão candidata.")
    selected = (
        record.published_revision
        if operation in {Op.REPUBLISH, Op.WITHDRAW, Op.ARCHIVE}
        else record.current_revision
    )
    if selected_revision != selected:
        raise CatalogError(C.APPROVAL_STALE, "Operação selecionou revisão diferente da esperada.")
    if operation == Op.PUBLISH:
        if record.approved_revision != selected_revision:
            raise CatalogError(C.APPROVAL_STALE, "Revisão selecionada não foi aprovada.")
        if scope == EditorialScope.CANDIDATE and record.editorial_state != S.PUBLISHED:
            raise CatalogError(
                C.TRANSITION_FORBIDDEN, "Republicar antes de substituir a revisão pública."
            )
    if operation in {Op.PUBLISH, Op.REPUBLISH}:
        if readiness is None:
            raise CatalogError(C.EDITORIAL_PRECONDITION, "Validação de publicação obrigatória.")
        if (
            readiness.identity != record.identity
            or readiness.revision != selected_revision
            or not readiness.profile.strip()
        ):
            raise CatalogError(C.APPROVAL_STALE, "Validação não corresponde à revisão selecionada.")
    if operation == Op.PUBLISH:
        after = replace(
            record,
            version=record.version + 1,
            editorial_state=S.PUBLISHED,
            candidate_state=None,
            published_revision=selected_revision,
        )
    elif operation == Op.ARCHIVE:
        after = replace(
            record,
            version=record.version + 1,
            editorial_state=S.ARCHIVED,
            candidate_state=None,
            approved_revision=None,
        )
    else:
        approval = record.approved_revision
        if operation == Op.APPROVE:
            approval = selected_revision
        elif operation == Op.RETURN:
            approval = None
        after = replace(
            record,
            version=record.version + 1,
            approved_revision=approval,
            candidate_state=destination
            if scope == EditorialScope.CANDIDATE
            else record.candidate_state,
            editorial_state=destination
            if scope == EditorialScope.RECORD
            else record.editorial_state,
        )
    event = PreparedEditorialEvent(
        record.identity,
        source,
        destination,
        operation,
        scope,
        actor_id,
        occurred_at,
        selected_revision,
        record.version,
        after.version,
        justification,
    )
    return PreparedTransition(record, after, event)


def prepare_workspace_publish(
    record: EditorialRecord,
    *,
    expected_version: int,
    expected_current_revision: int,
    actor_id: str,
    occurred_at: datetime,
    readiness: PublicationReadiness,
) -> PreparedTransition:
    """Publish the current draft as one workspace action.

    The public contract intentionally has no submit/approve stages. Historical
    records in intermediate legacy states must finish through the legacy API
    before they can enter the workspace flow again.
    """
    record.require_expected(expected_version, expected_current_revision)
    scope = EditorialScope.CANDIDATE if record.candidate_state else EditorialScope.RECORD
    source = record.source(scope)
    if source != S.DRAFT:
        raise CatalogError(C.TRANSITION_FORBIDDEN, "Workspace só publica um rascunho.")
    if not actor_id.strip() or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise CatalogError(C.EDITORIAL_PRECONDITION, "Ator e instante com timezone obrigatórios.")
    if readiness.identity != record.identity or readiness.revision != record.current_revision:
        raise CatalogError(C.APPROVAL_STALE, "Validação não corresponde ao rascunho corrente.")
    after = replace(
        record,
        version=record.version + 1,
        editorial_state=S.PUBLISHED,
        candidate_state=None,
        approved_revision=record.current_revision,
        published_revision=record.current_revision,
    )
    event = PreparedEditorialEvent(
        record.identity,
        source,
        S.PUBLISHED,
        Op.PUBLISH,
        scope,
        actor_id,
        occurred_at,
        record.current_revision,
        record.version,
        record.version + 1,
        None,
    )
    return PreparedTransition(record, after, event)


def prepare_content_revision(
    record: EditorialRecord,
    revision: Revision,
    *,
    expected_version: int,
    expected_current_revision: int,
) -> EditorialRecord:
    """Metadata to save atomically with the next immutable snapshot, never on its own."""
    record.require_expected(expected_version, expected_current_revision)
    if revision.identity != record.identity or revision.number != record.current_revision + 1:
        raise CatalogError(C.REVISION_STALE, "Nova revisão deve preservar identidade e sequência.")
    if record.editorial_state not in {S.DRAFT, S.PUBLISHED}:
        raise CatalogError(
            C.TRANSITION_FORBIDDEN, "Devolver para edição antes de alterar conteúdo."
        )
    if record.candidate_state not in {None, S.DRAFT}:
        raise CatalogError(C.TRANSITION_FORBIDDEN, "Devolver candidata antes de alterar conteúdo.")
    return replace(
        record,
        current_revision=revision.number,
        version=record.version + 1,
        approved_revision=None,
        candidate_state=S.DRAFT if record.editorial_state == S.PUBLISHED else None,
    )
