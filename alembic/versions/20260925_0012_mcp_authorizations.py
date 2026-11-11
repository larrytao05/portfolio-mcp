from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260925_0012"
down_revision: str | None = "20260925_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_authorizations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "target_draft_id",
            sa.String(length=36),
            sa.ForeignKey("order_drafts.id"),
            nullable=False,
        ),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.Column("consumed_at", sa.String(length=32), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalidation_reason", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_mcp_authorizations_action_target_draft",
        "mcp_authorizations",
        ["action", "target_draft_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_authorizations_action_target_draft",
        table_name="mcp_authorizations",
    )
    op.drop_table("mcp_authorizations")
