"""Append-only audit storage; the calling application owns the transaction."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    select,
    text,
    tuple_,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from libris.infrastructure.database import Base
from libris.modules.bibliographic.domain.audit import (
    ActorType,
    AuditEvent,
    AuditEventType,
    RestorationKind,
)
from libris.modules.bibliographic.domain.editorial import EditorialScope, EditorialState
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.models import validate_history_limit as validate_page
from libris.modules.bibliographic.infrastructure.persistence import (
    SemanticResource,
    SemanticRevision,
)

# These checks are mirrored in the immutable Alembic migration, not imported by it.
AUDIT_CHECKS = {
    "ck_audit_type": "event_type IN ('record.created','record.revised','record.submitted',"
    "'record.approved','record.published','record.withdrawn','record.restored','record.superseded','record.returned','record.archived')",
    "ck_audit_revision": "revision_number > 0 AND "
    "(event_type <> 'record.created' OR revision_number = 1) AND "
    "(event_type <> 'record.revised' OR revision_number > 1)",
    "ck_audit_source": "length(trim(source)) > 0 AND length(trim(process)) > 0",
    "ck_audit_reason": "reason IS NULL OR length(trim(reason)) > 0",
    "ck_audit_actor": "(actor_id IS NULL AND actor_type IS NULL) OR "
    "(actor_id IS NOT NULL AND actor_type IS NOT NULL AND ("
    "(actor_type = 'human' AND actor_id ~ '^oidc:sha256:[0-9a-f]{64}$') OR "
    "(actor_type IN ('service','automated') AND "
    "actor_id ~ ('^' || actor_type || ':[a-z][a-z0-9_.-]{0,63}$'))))",
    "ck_audit_successor": "(event_type = 'record.superseded' AND related_resource_id IS NOT NULL "
    "AND related_resource_id <> resource_id AND reason IS NOT NULL) OR "
    "(event_type <> 'record.superseded' AND related_resource_id IS NULL)",
    "ck_audit_restore": "(event_type <> 'record.restored' AND restoration_kind IS NULL "
    "AND source_revision IS NULL) OR (event_type = 'record.restored' AND "
    "restoration_kind IS NOT NULL AND ((restoration_kind = 'content' AND "
    "source_revision IS NOT NULL AND "
    "source_revision > 0 AND source_revision < revision_number) OR "
    "(restoration_kind = 'editorial' AND source_revision IS NULL)))",
    "ck_audit_states": "(event_type IN ('record.created','record.revised','record.superseded') "
    "OR (event_type = 'record.restored' AND restoration_kind = 'content')) AND "
    "previous_state IS NULL AND new_state IS NULL AND scope IS NULL AND "
    "previous_version IS NULL AND new_version IS NULL OR ("
    "previous_state IS NOT NULL AND new_state IS NOT NULL AND scope IS NOT NULL AND "
    "previous_version IS NOT NULL AND new_version IS NOT NULL AND "
    "actor_type IS NOT NULL AND actor_type = 'human' AND "
    "previous_version > 0 AND new_version = previous_version + 1 AND ("
    "(event_type = 'record.submitted' AND previous_state = 'draft' AND new_state = 'in_review' "
    "AND scope IN ('record','candidate')) OR "
    "(event_type = 'record.approved' AND previous_state = 'in_review' AND new_state = 'approved' "
    "AND scope IN ('record','candidate')) OR "
    "(event_type = 'record.published' AND previous_state IN ('draft','approved') AND "
    "new_state = 'published' "
    "AND scope IN ('record','candidate')) OR "
    "(event_type = 'record.withdrawn' AND previous_state = 'published' AND new_state = 'withdrawn' "
    "AND scope = 'record' AND reason IS NOT NULL) OR "
    "(event_type = 'record.returned' AND previous_state IN ('in_review','approved') "
    "AND new_state = 'draft' AND scope IN ('record','candidate') AND reason IS NOT NULL) OR "
    "(event_type = 'record.archived' AND previous_state = 'withdrawn' "
    "AND new_state = 'archived' AND scope = 'record' AND reason IS NOT NULL) OR "
    "(event_type = 'record.restored' AND restoration_kind = 'editorial' AND "
    "previous_state = 'withdrawn' AND new_state = 'published' AND scope = 'record')))",
}


class SemanticAuditEvent(Base):
    __tablename__ = "experimental_semantic_audit_events"
    __table_args__ = (
        *(CheckConstraint(expression, name=name) for name, expression in AUDIT_CHECKS.items()),
        ForeignKeyConstraint(
            ["resource_id", "revision_number"],
            [
                "experimental_semantic_revisions.resource_id",
                "experimental_semantic_revisions.number",
            ],
            name="fk_audit_revision",
        ),
        ForeignKeyConstraint(
            ["resource_id", "source_revision"],
            [
                "experimental_semantic_revisions.resource_id",
                "experimental_semantic_revisions.number",
            ],
            name="fk_audit_source_revision",
        ),
        ForeignKeyConstraint(
            ["related_resource_id"],
            ["experimental_semantic_resources.id"],
            name="fk_audit_successor",
        ),
        Index("ix_audit_resource_time", "resource_id", "occurred_at", "event_id"),
        Index("ix_audit_resource_revision", "resource_id", "revision_number"),
        Index(
            "ix_audit_editorial_version",
            "resource_id",
            "new_version",
            postgresql_where=text("new_version IS NOT NULL"),
        ),
    )
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    resource_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(String)
    revision_number: Mapped[int]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str | None] = mapped_column(String)
    actor_type: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    process: Mapped[str] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String)
    previous_state: Mapped[str | None] = mapped_column(String)
    new_state: Mapped[str | None] = mapped_column(String)
    related_resource_id: Mapped[UUID | None]
    restoration_kind: Mapped[str | None] = mapped_column(String)
    source_revision: Mapped[int | None]
    scope: Mapped[str | None] = mapped_column(String)
    previous_version: Mapped[int | None]
    new_version: Mapped[int | None]


class AuditStore:
    async def append(self, session: AsyncSession, event: AuditEvent) -> None:
        linked = (
            await session.execute(
                select(SemanticResource.uri, SemanticRevision.profile)
                .join(SemanticRevision, SemanticResource.id == SemanticRevision.resource_id)
                .where(
                    SemanticResource.id == event.identity.internal_id,
                    SemanticRevision.number == event.revision_number,
                )
            )
        ).one_or_none()
        if linked is None:
            raise LookupError("Revisão relevante não encontrada.")
        if linked.uri != event.identity.uri or linked.profile != event.profile:
            raise ValueError("Identidade/perfil do evento divergem da revisão persistida.")
        session.add(
            SemanticAuditEvent(
                event_id=event.event_id,
                resource_id=event.identity.internal_id,
                event_type=event.event_type,
                revision_number=event.revision_number,
                occurred_at=event.occurred_at,
                actor_id=event.actor_id,
                actor_type=event.actor_type,
                source=event.source,
                process=event.process,
                reason=event.reason,
                previous_state=event.previous_state,
                new_state=event.new_state,
                related_resource_id=event.related_resource_id,
                restoration_kind=event.restoration_kind,
                source_revision=event.source_revision,
                scope=event.scope,
                previous_version=event.previous_version,
                new_version=event.new_version,
            )
        )
        await session.flush()

    def _decode(self, row: SemanticAuditEvent, uri: str, profile: str) -> AuditEvent:
        return AuditEvent(
            event_id=row.event_id,
            event_type=AuditEventType(row.event_type),
            identity=ResourceIdentity(row.resource_id, uri),
            revision_number=row.revision_number,
            occurred_at=row.occurred_at,
            source=row.source,
            process=row.process,
            profile=profile,
            actor_id=row.actor_id,
            actor_type=ActorType(row.actor_type) if row.actor_type is not None else None,
            reason=row.reason,
            previous_state=EditorialState(row.previous_state) if row.previous_state else None,
            new_state=EditorialState(row.new_state) if row.new_state else None,
            related_resource_id=row.related_resource_id,
            restoration_kind=RestorationKind(row.restoration_kind)
            if row.restoration_kind
            else None,
            source_revision=row.source_revision,
            scope=EditorialScope(row.scope) if row.scope else None,
            previous_version=row.previous_version,
            new_version=row.new_version,
        )

    async def get(self, session: AsyncSession, identifier: UUID, event_id: UUID) -> AuditEvent:
        row = (
            await session.execute(
                select(SemanticAuditEvent, SemanticResource.uri, SemanticRevision.profile)
                .join(SemanticResource, SemanticAuditEvent.resource_id == SemanticResource.id)
                .join(
                    SemanticRevision,
                    (SemanticRevision.resource_id == SemanticAuditEvent.resource_id)
                    & (SemanticRevision.number == SemanticAuditEvent.revision_number),
                )
                .where(
                    SemanticAuditEvent.resource_id == identifier,
                    SemanticAuditEvent.event_id == event_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise LookupError("Evento não encontrado para este recurso.")
        return self._decode(row[0], row[1], row[2])

    async def list(
        self,
        session: AsyncSession,
        identifier: UUID,
        *,
        limit: int = 100,
        after: tuple[datetime, UUID] | None = None,
    ) -> tuple[AuditEvent, ...]:
        validate_page(limit)
        if after is not None and (after[0].tzinfo is None or after[0].utcoffset() is None):
            raise ValueError("Cursor exige timezone.")
        if await session.get(SemanticResource, identifier) is None:
            raise LookupError("Recurso não encontrado.")
        statement = (
            select(SemanticAuditEvent, SemanticResource.uri, SemanticRevision.profile)
            .join(SemanticResource, SemanticAuditEvent.resource_id == SemanticResource.id)
            .join(
                SemanticRevision,
                (SemanticRevision.resource_id == SemanticAuditEvent.resource_id)
                & (SemanticRevision.number == SemanticAuditEvent.revision_number),
            )
            .where(SemanticAuditEvent.resource_id == identifier)
        )
        if after is not None:
            statement = statement.where(
                tuple_(SemanticAuditEvent.occurred_at, SemanticAuditEvent.event_id) > after
            )
        rows = (
            await session.execute(
                statement.order_by(
                    SemanticAuditEvent.occurred_at, SemanticAuditEvent.event_id
                ).limit(limit)
            )
        ).all()
        return tuple(self._decode(row, uri, profile) for row, uri, profile in rows)

    async def content_revision_numbers(
        self,
        session: AsyncSession,
        identifier: UUID,
        numbers: tuple[int, ...],
    ) -> frozenset[int]:
        if not numbers:
            return frozenset()
        values = await session.scalars(
            select(SemanticAuditEvent.revision_number).where(
                SemanticAuditEvent.resource_id == identifier,
                SemanticAuditEvent.revision_number.in_(numbers),
                (SemanticAuditEvent.event_type.in_(["record.created", "record.revised"]))
                | (
                    (SemanticAuditEvent.event_type == "record.restored")
                    & (SemanticAuditEvent.restoration_kind == "content")
                ),
            )
        )
        return frozenset(values)
