"""LC Languages identifiers and single-active-release invariant."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_lc_languages"
down_revision: str | None = "0006_vocabulary_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vocabulary_identifiers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "release_concept_id",
            sa.Uuid(),
            sa.ForeignKey("vocabulary_release_concepts.id"),
            nullable=False,
        ),
        sa.Column("scheme", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("source_uri", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "release_concept_id", "scheme", "value", name="uq_vocabulary_identifier"
        ),
        sa.CheckConstraint(
            "scheme IN ('marc','iso639-1','iso639-2-b','iso639-2-t','iso639-3')",
            name="ck_vocabulary_identifier_scheme",
        ),
        sa.CheckConstraint("value = lower(value)", name="ck_vocabulary_identifier_value"),
    )
    op.create_index(
        "ix_vocabulary_identifier_lookup", "vocabulary_identifiers", ["scheme", "value"]
    )
    op.create_index(
        "uq_vocabulary_release_active",
        "vocabulary_releases",
        ["vocabulary_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM vocabulary_identifiers LIMIT 1) THEN
            RAISE EXCEPTION
                'Downgrade M08.03 exige remoção controlada dos identificadores';
        END IF;
        END $$""")
    op.drop_index("uq_vocabulary_release_active", table_name="vocabulary_releases")
    op.drop_table("vocabulary_identifiers")
