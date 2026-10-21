"""Observed operations, independent of RDF snapshots and storage."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from .editorial import EditorialScope, EditorialState
from .models import ResourceIdentity


class AuditEventType(StrEnum):
    CREATED = "record.created"
    REVISED = "record.revised"
    SUBMITTED = "record.submitted"
    APPROVED = "record.approved"
    PUBLISHED = "record.published"
    WITHDRAWN = "record.withdrawn"
    RESTORED = "record.restored"
    SUPERSEDED = "record.superseded"
    RETURNED = "record.returned"
    ARCHIVED = "record.archived"


class ActorType(StrEnum):
    HUMAN = "human"
    SERVICE = "service"
    AUTOMATED = "automated"


class RestorationKind(StrEnum):
    CONTENT = "content"
    EDITORIAL = "editorial"


@dataclass(frozen=True)
class ExecutionActor:
    """Trusted server configuration, never a request payload or anonymous identity."""

    actor_id: str
    actor_type: ActorType

    def __post_init__(self) -> None:
        kind = ActorType(self.actor_type)
        object.__setattr__(self, "actor_type", kind)
        if kind == ActorType.HUMAN or not re.fullmatch(
            rf"{kind.value}:[a-z][a-z0-9_.-]{{0,63}}", self.actor_id
        ):
            raise ValueError("Identidade técnica deve usar namespace controlado service/automated.")


@dataclass(frozen=True)
class AuditEvent:
    event_id: UUID
    event_type: AuditEventType
    identity: ResourceIdentity
    revision_number: int
    occurred_at: datetime
    source: str
    process: str
    profile: str
    actor_id: str | None = None
    actor_type: ActorType | None = None
    reason: str | None = None
    previous_state: EditorialState | None = None
    new_state: EditorialState | None = None
    related_resource_id: UUID | None = None
    restoration_kind: RestorationKind | None = None
    source_revision: int | None = None
    scope: EditorialScope | None = None
    previous_version: int | None = None
    new_version: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID) or not isinstance(self.identity, ResourceIdentity):
            raise ValueError("Evento e recurso exigem identidade UUID válida.")
        object.__setattr__(self, "event_type", AuditEventType(self.event_type))
        for name, enum in (
            ("actor_type", ActorType),
            ("previous_state", EditorialState),
            ("new_state", EditorialState),
            ("restoration_kind", RestorationKind),
            ("scope", EditorialScope),
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, enum(value))
        if type(self.revision_number) is not int or self.revision_number < 1:
            raise ValueError("Revisão relevante deve ser positiva.")
        if not isinstance(self.occurred_at, datetime) or (
            self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("Instante exige timezone.")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        for value in (self.source, self.process, self.profile):
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ValueError("Origem, processo e perfil são obrigatórios.")
        if self.reason is not None and (not self.reason.strip() or "\x00" in self.reason):
            raise ValueError("Justificativa fornecida deve ser não vazia.")
        if (self.actor_id is None) != (self.actor_type is None):
            raise ValueError("Ator desconhecido não possui identificador nem tipo presumido.")
        if self.actor_type == ActorType.HUMAN:
            if not re.fullmatch(r"oidc:sha256:[0-9a-f]{64}", self.actor_id or ""):
                raise ValueError("Ator humano exige identidade OIDC.")
        elif self.actor_type is not None:
            ExecutionActor(self.actor_id or "", self.actor_type)
        if self.related_resource_id is not None and (
            not isinstance(self.related_resource_id, UUID)
            or self.related_resource_id == self.identity.internal_id
        ):
            raise ValueError("Sucessor deve ser outro recurso UUID.")
        if (self.event_type == AuditEventType.SUPERSEDED) != (self.related_resource_id is not None):
            raise ValueError("Somente substituição exige sucessor explícito.")
        if self.event_type == AuditEventType.CREATED and self.revision_number != 1:
            raise ValueError("Criação referencia revisão inicial.")
        if self.event_type == AuditEventType.REVISED and self.revision_number < 2:
            raise ValueError("Revisão de conteúdo referencia revisão posterior à inicial.")
        if self.event_type == AuditEventType.RESTORED:
            if self.restoration_kind is None:
                raise ValueError("Restauração exige escopo content/editorial explícito.")
        elif self.restoration_kind is not None or self.source_revision is not None:
            raise ValueError("Origem histórica aplica-se somente à restauração de conteúdo.")
        content_restore = self.restoration_kind == RestorationKind.CONTENT
        if content_restore:
            if type(self.source_revision) is not int or not (
                1 <= self.source_revision < self.revision_number
            ):
                raise ValueError("Restauração de conteúdo exige revisão de origem anterior.")
        elif self.source_revision is not None:
            raise ValueError("Restauração editorial não copia conteúdo.")
        transitions = {
            AuditEventType.SUBMITTED: (EditorialState.DRAFT, EditorialState.IN_REVIEW),
            AuditEventType.APPROVED: (EditorialState.IN_REVIEW, EditorialState.APPROVED),
            AuditEventType.WITHDRAWN: (EditorialState.PUBLISHED, EditorialState.WITHDRAWN),
        }
        transitions[AuditEventType.ARCHIVED] = (EditorialState.WITHDRAWN, EditorialState.ARCHIVED)
        if self.event_type == AuditEventType.RETURNED:
            if self.previous_state not in {EditorialState.IN_REVIEW, EditorialState.APPROVED}:
                raise ValueError("Devolução exige revisão/aprovação anterior.")
            transitions[AuditEventType.RETURNED] = (self.previous_state, EditorialState.DRAFT)
        pair = transitions.get(self.event_type)
        if self.event_type == AuditEventType.PUBLISHED:
            if self.previous_state not in {EditorialState.DRAFT, EditorialState.APPROVED}:
                raise ValueError("Publicação exige rascunho de workspace ou aprovação legada.")
            pair = (self.previous_state, EditorialState.PUBLISHED)
        if self.restoration_kind == RestorationKind.EDITORIAL:
            pair = (EditorialState.WITHDRAWN, EditorialState.PUBLISHED)
        if pair is None:
            if any(
                v is not None
                for v in (
                    self.previous_state,
                    self.new_state,
                    self.scope,
                    self.previous_version,
                    self.new_version,
                )
            ):
                raise ValueError("Operação de conteúdo/identidade não altera estado editorial.")
        else:
            if (self.previous_state, self.new_state) != pair or self.scope is None:
                raise ValueError("Estados/escopo incompatíveis com a operação editorial.")
            if (
                type(self.previous_version) is not int
                or type(self.new_version) is not int
                or (self.previous_version < 1 or self.new_version != self.previous_version + 1)
            ):
                raise ValueError("Transição exige versões editoriais sequenciais.")
            if self.actor_type != ActorType.HUMAN:
                raise ValueError("Política editorial atual exige humano autenticado.")
            if self.scope == EditorialScope.CANDIDATE and self.event_type not in {
                AuditEventType.SUBMITTED,
                AuditEventType.APPROVED,
                AuditEventType.PUBLISHED,
                AuditEventType.RETURNED,
            }:
                raise ValueError("Operação não se aplica à candidata.")
        if self.event_type in {
            AuditEventType.WITHDRAWN,
            AuditEventType.SUPERSEDED,
            AuditEventType.RETURNED,
            AuditEventType.ARCHIVED,
        } and (self.reason is None):
            raise ValueError("Retirada/substituição exige justificativa.")
