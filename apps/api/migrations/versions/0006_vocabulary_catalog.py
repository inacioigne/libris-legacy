"""Persistent operational vocabulary catalog, releases, evidence and attempts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_vocabulary_catalog"
down_revision: str | None = "0005_web_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vocabulary_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("provider_uri", sa.String(), nullable=False),
        sa.Column("source_uri", sa.String(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(key)) > 0", name="ck_vocabulary_source_key"),
        sa.CheckConstraint(
            "length(trim(provider_uri)) > 0", name="ck_vocabulary_source_provider"
        ),
        sa.CheckConstraint("length(trim(source_uri)) > 0", name="ck_vocabulary_source_uri"),
    )
    op.create_table(
        "vocabularies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("uri", sa.String(), nullable=False, unique=True),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("vocabulary_sources.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('external','local')", name="ck_vocabulary_kind"),
        sa.CheckConstraint(
            "status IN ('draft','active','suspended','retired')", name="ck_vocabulary_status"
        ),
    )
    op.create_table(
        "vocabulary_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("vocabulary_id", sa.Uuid(), sa.ForeignKey("vocabularies.id"), nullable=False),
        sa.Column("release_key", sa.String(), nullable=False),
        sa.Column("source_version", sa.String(), nullable=True),
        sa.Column("source_uri", sa.String(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("completeness", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("vocabulary_id", "release_key", name="uq_vocabulary_release_key"),
        sa.UniqueConstraint("vocabulary_id", "digest", name="uq_vocabulary_release_digest"),
        sa.UniqueConstraint("id", "vocabulary_id", name="uq_vocabulary_release_identity"),
        sa.CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_vocabulary_release_digest"),
        sa.CheckConstraint(
            "completeness IN ('complete','partial')", name="ck_vocabulary_release_completeness"
        ),
        sa.CheckConstraint(
            "status IN ('importing','candidate','active','rejected','superseded')",
            name="ck_vocabulary_release_status",
        ),
        sa.CheckConstraint(
            "completeness = 'complete' OR length(trim(scope)) > 0",
            name="ck_vocabulary_release_partial_scope",
        ),
    )
    op.create_index(
        "ix_vocabulary_release_vocabulary", "vocabulary_releases", ["vocabulary_id", "created_at"]
    )
    op.create_table(
        "vocabulary_concepts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("vocabulary_id", sa.Uuid(), sa.ForeignKey("vocabularies.id"), nullable=False),
        sa.Column("uri", sa.String(), nullable=False, unique=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("internal_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(kind = 'external' AND internal_id IS NULL) OR "
            "(kind = 'local' AND internal_id IS NOT NULL)",
            name="ck_vocabulary_concept_identity",
        ),
        sa.UniqueConstraint("id", "uri", "vocabulary_id", name="uq_vocabulary_concept_identity"),
        sa.UniqueConstraint("internal_id", name="uq_vocabulary_concept_internal_id"),
    )
    op.create_table(
        "vocabulary_release_concepts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("concept_id", sa.Uuid(), nullable=False),
        sa.Column("vocabulary_id", sa.Uuid(), nullable=False),
        sa.Column("concept_uri", sa.String(), nullable=False),
        sa.Column("notation", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_uri", sa.String(), nullable=True),
        sa.Column("replacement_uri", sa.String(), nullable=True),
        sa.UniqueConstraint("release_id", "concept_id", name="uq_vocabulary_release_concept"),
        sa.ForeignKeyConstraint(
            ["release_id", "vocabulary_id"],
            ["vocabulary_releases.id", "vocabulary_releases.vocabulary_id"],
            name="fk_vocabulary_release_concept_release",
        ),
        sa.ForeignKeyConstraint(
            ["concept_id", "concept_uri", "vocabulary_id"],
            [
                "vocabulary_concepts.id",
                "vocabulary_concepts.uri",
                "vocabulary_concepts.vocabulary_id",
            ],
            name="fk_vocabulary_release_concept_identity",
        ),
        sa.CheckConstraint(
            "status IN ('resolved','unverified','invalid','deprecated','unavailable')",
            name="ck_vocabulary_release_concept_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('resolved','invalid','deprecated') OR source_uri IS NOT NULL",
            name="ck_vocabulary_release_concept_source",
        ),
        sa.CheckConstraint(
            "replacement_uri IS NULL OR "
            "(status = 'deprecated' AND replacement_uri <> concept_uri)",
            name="ck_vocabulary_release_concept_replacement",
        ),
    )
    op.create_index(
        "ix_vocabulary_release_concept_release",
        "vocabulary_release_concepts",
        ["release_id", "concept_uri"],
    )
    op.create_index(
        "ix_vocabulary_release_concept_notation",
        "vocabulary_release_concepts",
        ["release_id", "notation"],
    )
    op.create_table(
        "vocabulary_labels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "release_concept_id",
            sa.Uuid(),
            sa.ForeignKey("vocabulary_release_concepts.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False, server_default=""),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("source_uri", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "release_concept_id", "kind", "language", "value", name="uq_vocabulary_label"
        ),
        sa.CheckConstraint("kind IN ('preferred','alternative')", name="ck_vocabulary_label_kind"),
        sa.CheckConstraint("length(value) > 0", name="ck_vocabulary_label_value"),
        sa.CheckConstraint("language = lower(language)", name="ck_vocabulary_label_language"),
        sa.CheckConstraint(
            "origin IN ('provider_official','authorized_secondary',"
            "'institutional_translation','bibliographic_snapshot')",
            name="ck_vocabulary_label_origin",
        ),
    )
    op.create_index(
        "uq_vocabulary_preferred_language",
        "vocabulary_labels",
        ["release_concept_id", "language"],
        unique=True,
        postgresql_where=sa.text("kind = 'preferred'"),
    )
    op.create_index(
        "ix_vocabulary_label_lookup", "vocabulary_labels", ["language", "value"]
    )
    op.create_table(
        "vocabulary_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("vocabulary_id", sa.Uuid(), sa.ForeignKey("vocabularies.id"), nullable=False),
        sa.Column(
            "release_id", sa.Uuid(), sa.ForeignKey("vocabulary_releases.id"), nullable=True
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("vocabulary_id", "idempotency_key", name="uq_vocabulary_import_key"),
        sa.CheckConstraint(
            "status IN ('running','completed','failed')", name="ck_vocabulary_import_status"
        ),
    )
    op.create_table(
        "vocabulary_resolution_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("vocabulary_id", sa.Uuid(), sa.ForeignKey("vocabularies.id"), nullable=False),
        sa.Column(
            "release_id", sa.Uuid(), sa.ForeignKey("vocabulary_releases.id"), nullable=True
        ),
        sa.Column("concept_uri", sa.String(), nullable=False),
        sa.Column("provider_uri", sa.String(), nullable=False),
        sa.Column("source_uri", sa.String(), nullable=False),
        sa.Column("source_version", sa.String(), nullable=True),
        sa.Column("configuration_version", sa.String(), nullable=False),
        sa.Column("context", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("reliable", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('success','temporary_failure','permanent_failure')",
            name="ck_vocabulary_attempt_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'success' AND reason IS NULL AND reliable) OR "
            "(outcome <> 'success' AND reason IS NOT NULL AND NOT reliable)",
            name="ck_vocabulary_attempt_result",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ('unavailable','not_found','unsupported','access_denied')",
            name="ck_vocabulary_attempt_reason",
        ),
        sa.CheckConstraint("expires_at >= retrieved_at", name="ck_vocabulary_attempt_expiry"),
        sa.CheckConstraint(
            "stale_until IS NULL OR stale_until >= expires_at", name="ck_vocabulary_attempt_stale"
        ),
    )
    op.create_index(
        "ix_vocabulary_attempt_latest",
        "vocabulary_resolution_attempts",
        ["concept_uri", "provider_uri", "configuration_version", "context", "retrieved_at"],
    )
    op.create_index(
        "ix_vocabulary_evidence_latest",
        "vocabulary_resolution_attempts",
        ["concept_uri", "provider_uri", "retrieved_at"],
        postgresql_where=sa.text("reliable AND outcome = 'success'"),
    )


def downgrade() -> None:
    # Never erase operational evidence implicitly. Populated downgrade is intentionally refused.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM vocabularies LIMIT 1) THEN
            RAISE EXCEPTION 'Downgrade M08.02 exige exportação e remoção controlada dos dados';
        END IF;
        END $$""")
    for table in (
        "vocabulary_resolution_attempts",
        "vocabulary_imports",
        "vocabulary_labels",
        "vocabulary_release_concepts",
        "vocabulary_concepts",
        "vocabulary_releases",
        "vocabularies",
        "vocabulary_sources",
    ):
        op.drop_table(table)
