"""PostgreSQL models and repository for the operational vocabulary catalog."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    case,
    func,
    or_,
    select,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from libris.infrastructure.database import Base


class VocabularySourceRecord(Base):
    __tablename__ = "vocabulary_sources"
    __table_args__ = (
        CheckConstraint("length(trim(key)) > 0", name="ck_vocabulary_source_key"),
        CheckConstraint("length(trim(provider_uri)) > 0", name="ck_vocabulary_source_provider"),
        CheckConstraint("length(trim(source_uri)) > 0", name="ck_vocabulary_source_uri"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True)
    provider_uri: Mapped[str] = mapped_column(String)
    source_uri: Mapped[str] = mapped_column(String)
    provenance: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VocabularyRecord(Base):
    __tablename__ = "vocabularies"
    __table_args__ = (
        CheckConstraint("kind IN ('external','local')", name="ck_vocabulary_kind"),
        CheckConstraint(
            "status IN ('draft','active','suspended','retired')", name="ck_vocabulary_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    uri: Mapped[str] = mapped_column(String, unique=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("vocabulary_sources.id"))
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VocabularyReleaseRecord(Base):
    __tablename__ = "vocabulary_releases"
    __table_args__ = (
        UniqueConstraint("vocabulary_id", "release_key", name="uq_vocabulary_release_key"),
        UniqueConstraint("vocabulary_id", "digest", name="uq_vocabulary_release_digest"),
        UniqueConstraint("id", "vocabulary_id", name="uq_vocabulary_release_identity"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_vocabulary_release_digest"),
        CheckConstraint(
            "completeness IN ('complete','partial')", name="ck_vocabulary_release_completeness"
        ),
        CheckConstraint(
            "status IN ('importing','candidate','active','rejected','superseded')",
            name="ck_vocabulary_release_status",
        ),
        CheckConstraint(
            "completeness = 'complete' OR length(trim(scope)) > 0",
            name="ck_vocabulary_release_partial_scope",
        ),
        Index("ix_vocabulary_release_vocabulary", "vocabulary_id", "created_at"),
        Index(
            "uq_vocabulary_release_active",
            "vocabulary_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    vocabulary_id: Mapped[UUID] = mapped_column(ForeignKey("vocabularies.id"))
    release_key: Mapped[str] = mapped_column(String)
    source_version: Mapped[str | None] = mapped_column(String, nullable=True)
    source_uri: Mapped[str] = mapped_column(String)
    digest: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    completeness: Mapped[str] = mapped_column(String)
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VocabularyConceptRecord(Base):
    __tablename__ = "vocabulary_concepts"
    __table_args__ = (
        UniqueConstraint("internal_id", name="uq_vocabulary_concept_internal_id"),
        UniqueConstraint("id", "uri", "vocabulary_id", name="uq_vocabulary_concept_identity"),
        CheckConstraint(
            "(kind = 'external' AND internal_id IS NULL) OR "
            "(kind = 'local' AND internal_id IS NOT NULL)",
            name="ck_vocabulary_concept_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    vocabulary_id: Mapped[UUID] = mapped_column(ForeignKey("vocabularies.id"))
    uri: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)
    internal_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VocabularyReleaseConceptRecord(Base):
    __tablename__ = "vocabulary_release_concepts"
    __table_args__ = (
        UniqueConstraint("release_id", "concept_id", name="uq_vocabulary_release_concept"),
        ForeignKeyConstraint(
            ["release_id", "vocabulary_id"],
            ["vocabulary_releases.id", "vocabulary_releases.vocabulary_id"],
            name="fk_vocabulary_release_concept_release",
        ),
        ForeignKeyConstraint(
            ["concept_id", "concept_uri", "vocabulary_id"],
            [
                "vocabulary_concepts.id",
                "vocabulary_concepts.uri",
                "vocabulary_concepts.vocabulary_id",
            ],
            name="fk_vocabulary_release_concept_identity",
        ),
        CheckConstraint(
            "status IN ('resolved','unverified','invalid','deprecated','unavailable')",
            name="ck_vocabulary_release_concept_status",
        ),
        CheckConstraint(
            "status NOT IN ('resolved','invalid','deprecated') OR source_uri IS NOT NULL",
            name="ck_vocabulary_release_concept_source",
        ),
        CheckConstraint(
            "(replacement_uri IS NULL) OR "
            "(status = 'deprecated' AND replacement_uri <> concept_uri)",
            name="ck_vocabulary_release_concept_replacement",
        ),
        Index("ix_vocabulary_release_concept_release", "release_id", "concept_uri"),
        Index("ix_vocabulary_release_concept_notation", "release_id", "notation"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    release_id: Mapped[UUID]
    concept_id: Mapped[UUID]
    vocabulary_id: Mapped[UUID]
    concept_uri: Mapped[str] = mapped_column(String)
    notation: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    source_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    replacement_uri: Mapped[str | None] = mapped_column(String, nullable=True)


class VocabularyLabelRecord(Base):
    __tablename__ = "vocabulary_labels"
    __table_args__ = (
        UniqueConstraint(
            "release_concept_id", "kind", "language", "value", name="uq_vocabulary_label"
        ),
        CheckConstraint("kind IN ('preferred','alternative')", name="ck_vocabulary_label_kind"),
        CheckConstraint("length(value) > 0", name="ck_vocabulary_label_value"),
        CheckConstraint("language = lower(language)", name="ck_vocabulary_label_language"),
        CheckConstraint(
            "origin IN ('provider_official','authorized_secondary',"
            "'institutional_translation','bibliographic_snapshot')",
            name="ck_vocabulary_label_origin",
        ),
        Index("ix_vocabulary_label_lookup", "language", "value"),
        Index(
            "uq_vocabulary_preferred_language",
            "release_concept_id",
            "language",
            unique=True,
            postgresql_where=text("kind = 'preferred'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    release_concept_id: Mapped[UUID] = mapped_column(ForeignKey("vocabulary_release_concepts.id"))
    kind: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    language: Mapped[str] = mapped_column(String, default="")
    origin: Mapped[str] = mapped_column(String)
    source_uri: Mapped[str] = mapped_column(String)


class VocabularyIdentifierRecord(Base):
    __tablename__ = "vocabulary_identifiers"
    __table_args__ = (
        UniqueConstraint("release_concept_id", "scheme", "value", name="uq_vocabulary_identifier"),
        CheckConstraint(
            "scheme IN ('marc','iso639-1','iso639-2-b','iso639-2-t','iso639-3')",
            name="ck_vocabulary_identifier_scheme",
        ),
        CheckConstraint("value = lower(value)", name="ck_vocabulary_identifier_value"),
        Index("ix_vocabulary_identifier_lookup", "scheme", "value"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    release_concept_id: Mapped[UUID] = mapped_column(ForeignKey("vocabulary_release_concepts.id"))
    scheme: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    source_uri: Mapped[str] = mapped_column(String)


class VocabularyImportRecord(Base):
    __tablename__ = "vocabulary_imports"
    __table_args__ = (
        UniqueConstraint("vocabulary_id", "idempotency_key", name="uq_vocabulary_import_key"),
        CheckConstraint(
            "status IN ('running','completed','failed')", name="ck_vocabulary_import_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    vocabulary_id: Mapped[UUID] = mapped_column(ForeignKey("vocabularies.id"))
    release_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vocabulary_releases.id"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String)
    payload_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VocabularyResolutionAttemptRecord(Base):
    __tablename__ = "vocabulary_resolution_attempts"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success','temporary_failure','permanent_failure')",
            name="ck_vocabulary_attempt_outcome",
        ),
        CheckConstraint(
            "(outcome = 'success' AND reason IS NULL AND reliable) OR "
            "(outcome <> 'success' AND reason IS NOT NULL AND NOT reliable)",
            name="ck_vocabulary_attempt_result",
        ),
        CheckConstraint(
            "reason IS NULL OR reason IN ('unavailable','not_found','unsupported','access_denied')",
            name="ck_vocabulary_attempt_reason",
        ),
        CheckConstraint("expires_at >= retrieved_at", name="ck_vocabulary_attempt_expiry"),
        CheckConstraint(
            "stale_until IS NULL OR stale_until >= expires_at", name="ck_vocabulary_attempt_stale"
        ),
        Index(
            "ix_vocabulary_attempt_latest",
            "concept_uri",
            "provider_uri",
            "configuration_version",
            "context",
            "retrieved_at",
        ),
        Index(
            "ix_vocabulary_evidence_latest",
            "concept_uri",
            "provider_uri",
            "retrieved_at",
            postgresql_where=text("reliable AND outcome = 'success'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    vocabulary_id: Mapped[UUID] = mapped_column(ForeignKey("vocabularies.id"))
    release_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vocabulary_releases.id"), nullable=True
    )
    concept_uri: Mapped[str] = mapped_column(String)
    provider_uri: Mapped[str] = mapped_column(String)
    source_uri: Mapped[str] = mapped_column(String)
    source_version: Mapped[str | None] = mapped_column(String, nullable=True)
    configuration_version: Mapped[str] = mapped_column(String)
    context: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    reliable: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stale_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_after_seconds: Mapped[int | None] = mapped_column(nullable=True)


@dataclass(frozen=True)
class VocabularyListingRow:
    vocabulary: VocabularyRecord
    source: VocabularySourceRecord
    release: VocabularyReleaseRecord


@dataclass(frozen=True)
class VocabularyConceptSearchRow:
    concept: VocabularyConceptRecord
    occurrence: VocabularyReleaseConceptRecord
    release: VocabularyReleaseRecord
    vocabulary: VocabularyRecord
    source: VocabularySourceRecord
    label: str | None
    label_language: str | None
    label_origin: str | None
    label_source_uri: str | None


@dataclass(frozen=True)
class VocabularyConceptDetailRow:
    concept: VocabularyConceptRecord
    occurrence: VocabularyReleaseConceptRecord
    release: VocabularyReleaseRecord
    vocabulary: VocabularyRecord
    source: VocabularySourceRecord


@dataclass(frozen=True)
class VocabularyMembershipSnapshot:
    vocabulary_uri: str
    release_key: str
    source_uri: str
    completeness: str
    concepts: tuple[tuple[str, str, str | None, str | None], ...]


class VocabularyCatalogRepository:
    """Persistence primitives; transaction ownership remains with the application."""

    async def available_vocabularies(
        self, session: AsyncSession
    ) -> tuple[VocabularyListingRow, ...]:
        rows = await session.execute(
            select(VocabularyRecord, VocabularySourceRecord, VocabularyReleaseRecord)
            .join(VocabularySourceRecord, VocabularySourceRecord.id == VocabularyRecord.source_id)
            .join(
                VocabularyReleaseRecord,
                VocabularyReleaseRecord.vocabulary_id == VocabularyRecord.id,
            )
            .where(
                VocabularyRecord.status == "active",
                VocabularyReleaseRecord.status == "active",
            )
            .order_by(VocabularyRecord.uri, VocabularyRecord.id)
        )
        return tuple(
            VocabularyListingRow(vocabulary, source, release)
            for vocabulary, source, release in rows
        )

    async def vocabulary_by_id(
        self, session: AsyncSession, vocabulary_id: UUID
    ) -> VocabularyRecord | None:
        vocabulary = await session.get(VocabularyRecord, vocabulary_id)
        if vocabulary is None or vocabulary.status != "active":
            return None
        return vocabulary

    async def query_release(
        self,
        session: AsyncSession,
        vocabulary_id: UUID,
        release_id: UUID | None,
    ) -> VocabularyReleaseRecord | None:
        statement = select(VocabularyReleaseRecord).where(
            VocabularyReleaseRecord.vocabulary_id == vocabulary_id
        )
        if release_id is None:
            statement = statement.where(VocabularyReleaseRecord.status == "active")
        else:
            statement = statement.where(
                VocabularyReleaseRecord.id == release_id,
                VocabularyReleaseRecord.status.in_(("active", "superseded")),
            )
        rows = await session.scalars(statement)
        return rows.one_or_none()

    async def search_concepts(
        self,
        session: AsyncSession,
        *,
        vocabulary_id: UUID,
        release_id: UUID,
        query: str | None,
        uri: str | None,
        status: str | None,
        label_language: str | None,
        locale_fallback: tuple[str, ...],
        limit: int,
        offset: int,
    ) -> tuple[int, tuple[VocabularyConceptSearchRow, ...]]:
        conditions = [
            VocabularyReleaseConceptRecord.vocabulary_id == vocabulary_id,
            VocabularyReleaseConceptRecord.release_id == release_id,
        ]
        if status is not None:
            conditions.append(VocabularyReleaseConceptRecord.status == status)
        if uri is not None:
            conditions.append(VocabularyReleaseConceptRecord.concept_uri == uri)
        if query is not None:
            term = query.lower()
            matching_label = (
                select(VocabularyLabelRecord.id)
                .where(
                    VocabularyLabelRecord.release_concept_id
                    == VocabularyReleaseConceptRecord.id,
                    func.lower(
                        func.regexp_replace(
                            func.normalize(VocabularyLabelRecord.value),
                            "[[:space:]]+",
                            " ",
                            "g",
                        )
                    ).contains(term, autoescape=True),
                )
                .exists()
            )
            matching_identifier = (
                select(VocabularyIdentifierRecord.id)
                .where(
                    VocabularyIdentifierRecord.release_concept_id
                    == VocabularyReleaseConceptRecord.id,
                    VocabularyIdentifierRecord.value == term,
                )
                .exists()
            )
            conditions.append(
                or_(
                    func.lower(VocabularyReleaseConceptRecord.notation) == term,
                    matching_label,
                    matching_identifier,
                )
            )
        if label_language is not None:
            matching_locale = (
                select(VocabularyLabelRecord.id)
                .where(
                    VocabularyLabelRecord.release_concept_id
                    == VocabularyReleaseConceptRecord.id,
                    VocabularyLabelRecord.language == label_language,
                )
                .exists()
            )
            conditions.append(matching_locale)

        matching = (
            select(VocabularyReleaseConceptRecord.id)
            .where(*conditions)
            .subquery()
        )
        total = int(await session.scalar(select(func.count()).select_from(matching)) or 0)
        label_priority = case(
            *(
                (VocabularyLabelRecord.language == language, rank)
                for rank, language in enumerate(locale_fallback)
            ),
            else_=len(locale_fallback),
        )
        preferred_label = (
            select(
                VocabularyLabelRecord.value.label("value"),
                VocabularyLabelRecord.language.label("language"),
                VocabularyLabelRecord.origin.label("origin"),
                VocabularyLabelRecord.source_uri.label("source_uri"),
            )
            .where(
                VocabularyLabelRecord.release_concept_id
                == VocabularyReleaseConceptRecord.id
            )
            .order_by(
                case((VocabularyLabelRecord.kind == "preferred", 0), else_=1),
                label_priority,
                VocabularyLabelRecord.language,
                VocabularyLabelRecord.value,
            )
            .limit(1)
            .lateral("localized_label")
        )
        rows = await session.execute(
            select(
                VocabularyConceptRecord,
                VocabularyReleaseConceptRecord,
                VocabularyReleaseRecord,
                VocabularyRecord,
                VocabularySourceRecord,
                preferred_label.c.value,
                preferred_label.c.language,
                preferred_label.c.origin,
                preferred_label.c.source_uri,
            )
            .join(
                VocabularyReleaseConceptRecord,
                VocabularyReleaseConceptRecord.concept_id == VocabularyConceptRecord.id,
            )
            .join(
                VocabularyReleaseRecord,
                VocabularyReleaseRecord.id == VocabularyReleaseConceptRecord.release_id,
            )
            .join(VocabularyRecord, VocabularyRecord.id == VocabularyConceptRecord.vocabulary_id)
            .join(VocabularySourceRecord, VocabularySourceRecord.id == VocabularyRecord.source_id)
            .outerjoin(preferred_label, true())
            .where(*conditions)
            .order_by(
                VocabularyReleaseConceptRecord.concept_uri,
                VocabularyConceptRecord.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return total, tuple(
            VocabularyConceptSearchRow(
                concept,
                occurrence,
                release,
                vocabulary,
                source,
                label,
                label_language,
                label_origin,
                label_source_uri,
            )
            for (
                concept,
                occurrence,
                release,
                vocabulary,
                source,
                label,
                label_language,
                label_origin,
                label_source_uri,
            ) in rows
        )

    async def concept_detail(
        self,
        session: AsyncSession,
        *,
        vocabulary_id: UUID,
        release_id: UUID,
        concept_id: UUID,
    ) -> VocabularyConceptDetailRow | None:
        row = (
            await session.execute(
                select(
                    VocabularyConceptRecord,
                    VocabularyReleaseConceptRecord,
                    VocabularyReleaseRecord,
                    VocabularyRecord,
                    VocabularySourceRecord,
                )
                .join(
                    VocabularyReleaseConceptRecord,
                    VocabularyReleaseConceptRecord.concept_id == VocabularyConceptRecord.id,
                )
                .join(
                    VocabularyReleaseRecord,
                    VocabularyReleaseRecord.id == VocabularyReleaseConceptRecord.release_id,
                )
                .join(
                    VocabularyRecord,
                    VocabularyRecord.id == VocabularyConceptRecord.vocabulary_id,
                )
                .join(
                    VocabularySourceRecord,
                    VocabularySourceRecord.id == VocabularyRecord.source_id,
                )
                .where(
                    VocabularyConceptRecord.id == concept_id,
                    VocabularyConceptRecord.vocabulary_id == vocabulary_id,
                    VocabularyReleaseConceptRecord.release_id == release_id,
                )
            )
        ).one_or_none()
        return VocabularyConceptDetailRow(*row) if row is not None else None

    async def concept_labels(
        self, session: AsyncSession, occurrence_id: UUID
    ) -> tuple[VocabularyLabelRecord, ...]:
        rows = await session.scalars(
            select(VocabularyLabelRecord)
            .where(VocabularyLabelRecord.release_concept_id == occurrence_id)
            .order_by(
                case((VocabularyLabelRecord.kind == "preferred", 0), else_=1),
                VocabularyLabelRecord.language,
                VocabularyLabelRecord.value,
                VocabularyLabelRecord.id,
            )
        )
        return tuple(rows.all())

    async def concept_by_uri(
        self,
        session: AsyncSession,
        *,
        vocabulary_id: UUID,
        release_id: UUID,
        uri: str,
    ) -> VocabularyConceptDetailRow | None:
        row = (
            await session.execute(
                select(
                    VocabularyConceptRecord,
                    VocabularyReleaseConceptRecord,
                    VocabularyReleaseRecord,
                    VocabularyRecord,
                    VocabularySourceRecord,
                )
                .join(
                    VocabularyReleaseConceptRecord,
                    VocabularyReleaseConceptRecord.concept_id == VocabularyConceptRecord.id,
                )
                .join(
                    VocabularyReleaseRecord,
                    VocabularyReleaseRecord.id == VocabularyReleaseConceptRecord.release_id,
                )
                .join(
                    VocabularyRecord,
                    VocabularyRecord.id == VocabularyConceptRecord.vocabulary_id,
                )
                .join(
                    VocabularySourceRecord,
                    VocabularySourceRecord.id == VocabularyRecord.source_id,
                )
                .where(
                    VocabularyConceptRecord.uri == uri,
                    VocabularyConceptRecord.vocabulary_id == vocabulary_id,
                    VocabularyReleaseConceptRecord.release_id == release_id,
                )
            )
        ).one_or_none()
        return VocabularyConceptDetailRow(*row) if row is not None else None

    async def active_vocabulary_source(
        self, session: AsyncSession, vocabulary_id: UUID
    ) -> VocabularySourceRecord | None:
        rows = await session.scalars(
            select(VocabularySourceRecord)
            .join(VocabularyRecord, VocabularyRecord.source_id == VocabularySourceRecord.id)
            .where(
                VocabularyRecord.id == vocabulary_id,
                VocabularyRecord.status == "active",
            )
        )
        return rows.one_or_none()

    async def membership_snapshots(
        self, session: AsyncSession, vocabulary_uris: tuple[str, ...]
    ) -> tuple[VocabularyMembershipSnapshot, ...]:
        if not vocabulary_uris:
            return ()
        rows = await session.execute(
            select(
                VocabularyRecord.uri,
                VocabularyReleaseRecord.release_key,
                VocabularyReleaseRecord.source_uri,
                VocabularyReleaseRecord.completeness,
                VocabularyReleaseConceptRecord.concept_uri,
                VocabularyReleaseConceptRecord.status,
                VocabularyReleaseConceptRecord.source_uri,
                VocabularyReleaseConceptRecord.replacement_uri,
            )
            .join(
                VocabularyReleaseRecord,
                VocabularyReleaseRecord.vocabulary_id == VocabularyRecord.id,
            )
            .outerjoin(
                VocabularyReleaseConceptRecord,
                VocabularyReleaseConceptRecord.release_id == VocabularyReleaseRecord.id,
            )
            .where(
                VocabularyRecord.uri.in_(vocabulary_uris),
                VocabularyRecord.status == "active",
                VocabularyReleaseRecord.status == "active",
            )
            .order_by(VocabularyRecord.uri, VocabularyReleaseConceptRecord.concept_uri)
        )
        grouped: dict[
            tuple[str, str, str, str],
            list[tuple[str, str, str | None, str | None]],
        ] = {}
        for (
            vocabulary_uri,
            release_key,
            source_uri,
            completeness,
            concept_uri,
            status,
            concept_source_uri,
            replacement_uri,
        ) in rows:
            key = (vocabulary_uri, release_key, source_uri, completeness)
            grouped.setdefault(key, [])
            if concept_uri is not None and status is not None:
                grouped[key].append(
                    (concept_uri, status, concept_source_uri, replacement_uri)
                )
        return tuple(
            VocabularyMembershipSnapshot(
                vocabulary_uri=vocabulary_uri,
                release_key=release_key,
                source_uri=source_uri,
                completeness=completeness,
                concepts=tuple(concepts),
            )
            for (vocabulary_uri, release_key, source_uri, completeness), concepts
            in sorted(grouped.items())
        )

    async def vocabulary_by_uri(self, session: AsyncSession, uri: str) -> VocabularyRecord | None:
        rows = await session.scalars(select(VocabularyRecord).where(VocabularyRecord.uri == uri))
        return rows.one_or_none()

    async def release_by_key(
        self, session: AsyncSession, vocabulary_id: UUID, release_key: str
    ) -> VocabularyReleaseRecord | None:
        rows = await session.scalars(
            select(VocabularyReleaseRecord).where(
                VocabularyReleaseRecord.vocabulary_id == vocabulary_id,
                VocabularyReleaseRecord.release_key == release_key,
            )
        )
        return rows.one_or_none()

    async def releases(
        self, session: AsyncSession, vocabulary_id: UUID
    ) -> tuple[VocabularyReleaseRecord, ...]:
        return tuple(
            (
                await session.scalars(
                    select(VocabularyReleaseRecord)
                    .where(VocabularyReleaseRecord.vocabulary_id == vocabulary_id)
                    .order_by(VocabularyReleaseRecord.created_at, VocabularyReleaseRecord.id)
                )
            ).all()
        )

    async def latest_attempt(
        self,
        session: AsyncSession,
        *,
        uri: str,
        provider_uri: str,
        configuration_version: str,
        context: str,
    ) -> VocabularyResolutionAttemptRecord | None:
        rows = await session.scalars(
            select(VocabularyResolutionAttemptRecord)
            .where(
                VocabularyResolutionAttemptRecord.concept_uri == uri,
                VocabularyResolutionAttemptRecord.provider_uri == provider_uri,
                VocabularyResolutionAttemptRecord.configuration_version == configuration_version,
                VocabularyResolutionAttemptRecord.context == context,
            )
            .order_by(
                VocabularyResolutionAttemptRecord.retrieved_at.desc(),
                VocabularyResolutionAttemptRecord.id.desc(),
            )
            .limit(1)
        )
        return rows.one_or_none()

    async def latest_reliable_evidence(
        self,
        session: AsyncSession,
        *,
        uri: str,
        provider_uri: str,
        configuration_version: str,
        context: str,
    ) -> VocabularyResolutionAttemptRecord | None:
        rows = await session.scalars(
            select(VocabularyResolutionAttemptRecord)
            .where(
                VocabularyResolutionAttemptRecord.concept_uri == uri,
                VocabularyResolutionAttemptRecord.provider_uri == provider_uri,
                VocabularyResolutionAttemptRecord.configuration_version == configuration_version,
                VocabularyResolutionAttemptRecord.context == context,
                VocabularyResolutionAttemptRecord.outcome == "success",
                VocabularyResolutionAttemptRecord.reliable.is_(True),
            )
            .order_by(
                VocabularyResolutionAttemptRecord.retrieved_at.desc(),
                VocabularyResolutionAttemptRecord.id.desc(),
            )
            .limit(1)
        )
        return rows.one_or_none()
