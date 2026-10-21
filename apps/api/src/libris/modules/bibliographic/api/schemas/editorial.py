"""HTTP editorial inputs and derived state; no client-supplied identity context."""

from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from libris.modules.bibliographic.application.diagnostics import PublicContract
from libris.modules.bibliographic.application.history import RevisionHistoryEntry
from libris.modules.bibliographic.domain.audit import AuditEvent
from libris.modules.bibliographic.domain.editorial import EditorialScope, EditorialState


class TransitionRequest(PublicContract):
    selected_revision: int = Field(gt=0, strict=True, examples=[1])
    scope: EditorialScope = EditorialScope.RECORD
    justification: str | None = Field(default=None, max_length=2000, examples=["Revisar descrição"])

    @field_validator("justification")
    @classmethod
    def valid_justification(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("Justificativa deve ser não vazia e não conter NUL.")
        return value


class RestoreRequest(PublicContract):
    source_revision: int = Field(gt=0, strict=True, examples=[1])
    justification: str | None = Field(default=None, max_length=2000, examples=["Revisar descrição"])

    @field_validator("justification")
    @classmethod
    def valid_justification(cls, value: str | None) -> str | None:
        return TransitionRequest.valid_justification(value)


class EditorialStateRead(PublicContract):
    internal_id: UUID
    editorial_state: EditorialState | None
    current_revision: int
    candidate_state: EditorialState | None
    candidate_revision: int | None
    approved_revision: int | None
    published_revision: int | None
    public_revision: int | None
    editorial_version: int | None


class WorkspaceValidationRead(PublicContract):
    ready: bool = True
    revision: int = Field(gt=0, strict=True)


class EventCursor(PublicContract):
    occurred_at: AwareDatetime
    event_id: UUID


class EventQuery(PublicContract):
    limit: int = Field(default=100, ge=1, le=200)
    after_time: AwareDatetime | None = None
    after_event_id: UUID | None = None

    @model_validator(mode="after")
    def paired_cursor(self) -> "EventQuery":
        if (self.after_time is None) != (self.after_event_id is None):
            raise ValueError("Cursor exige instante e identificador do evento.")
        return self

    @property
    def after(self) -> tuple[datetime, UUID] | None:
        if self.after_time is None or self.after_event_id is None:
            return None
        return self.after_time, self.after_event_id


class EventPage(PublicContract):
    items: tuple[AuditEvent, ...]
    next_cursor: EventCursor | None


class RevisionPage(PublicContract):
    items: tuple[RevisionHistoryEntry, ...]
    next_after_number: int | None
