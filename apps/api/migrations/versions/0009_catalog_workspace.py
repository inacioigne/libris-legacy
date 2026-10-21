"""Allow one-step publication from a validated cataloging workspace draft."""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_catalog_workspace"
down_revision: str | None = "0008_vocabulary_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASE = (
    "(event_type IN ('record.created','record.revised','record.superseded') OR "
    "(event_type = 'record.restored' AND restoration_kind = 'content')) AND "
    "previous_state IS NULL AND new_state IS NULL AND scope IS NULL AND "
    "previous_version IS NULL AND new_version IS NULL OR (previous_state IS NOT NULL "
    "AND new_state IS NOT NULL AND scope IS NOT NULL AND previous_version IS NOT NULL "
    "AND new_version IS NOT NULL AND actor_type = 'human' AND previous_version > 0 "
    "AND new_version = previous_version + 1 AND ("
    "(event_type = 'record.submitted' AND previous_state = 'draft' AND "
    "new_state = 'in_review' AND scope IN ('record','candidate')) OR "
    "(event_type = 'record.approved' AND previous_state = 'in_review' AND "
    "new_state = 'approved' AND scope IN ('record','candidate')) OR "
)
TAIL = (
    "(event_type = 'record.withdrawn' AND previous_state = 'published' AND "
    "new_state = 'withdrawn' AND scope = 'record' AND reason IS NOT NULL) OR "
    "(event_type = 'record.returned' AND previous_state IN ('in_review','approved') AND "
    "new_state = 'draft' AND scope IN ('record','candidate') AND reason IS NOT NULL) OR "
    "(event_type = 'record.archived' AND previous_state = 'withdrawn' AND "
    "new_state = 'archived' AND scope = 'record' AND reason IS NOT NULL) OR "
    "(event_type = 'record.restored' AND restoration_kind = 'editorial' AND "
    "previous_state = 'withdrawn' AND new_state = 'published' AND scope = 'record')))"
)
LEGACY_PUBLISH = (
    "(event_type = 'record.published' AND previous_state = 'approved' AND "
    "new_state = 'published' AND scope IN ('record','candidate')) OR "
)
WORKSPACE_PUBLISH = (
    "(event_type = 'record.published' AND previous_state IN ('draft','approved') AND "
    "new_state = 'published' AND scope IN ('record','candidate')) OR "
)


def replace(expression: str) -> None:
    op.drop_constraint(
        "ck_audit_states", "experimental_semantic_audit_events", type_="check"
    )
    op.create_check_constraint(
        "ck_audit_states", "experimental_semantic_audit_events", expression
    )


def upgrade() -> None:
    replace(BASE + WORKSPACE_PUBLISH + TAIL)


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM experimental_semantic_audit_events
            WHERE event_type = 'record.published' AND previous_state = 'draft'
        ) THEN
            RAISE EXCEPTION
                'Downgrade do workspace exige preservar publicações diretas existentes';
        END IF;
        END $$""")
    replace(BASE + LEGACY_PUBLISH + TAIL)
