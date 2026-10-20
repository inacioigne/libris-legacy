"""Preserve source-specific provenance for each vocabulary release."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_vocabulary_release_provenance"
down_revision: str | None = "0007_lc_languages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vocabulary_releases",
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM vocabulary_releases WHERE provenance <> '{}'::jsonb LIMIT 1
        ) THEN
            RAISE EXCEPTION
                'Downgrade M08 release provenance exige exportação controlada dos dados';
        END IF;
        END $$""")
    op.drop_column("vocabulary_releases", "provenance")
