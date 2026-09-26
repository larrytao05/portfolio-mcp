from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260926_0013"
down_revision: str | None = "20260925_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cancellation_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "order_id",
            sa.String(length=36),
            sa.ForeignKey("orders.id"),
            nullable=False,
        ),
        sa.Column("expected_order_version", sa.Integer(), nullable=False),
        sa.Column("expected_order_state", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("remaining_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("action_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("invalidation_reason", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_cancellation_requests_order_status",
        "cancellation_requests",
        ["order_id", "status"],
    )

    with op.batch_alter_table("mcp_authorizations") as batch_op:
        batch_op.alter_column(
            "target_draft_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "target_cancellation_request_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "cancellation_requests.id",
                    name="fk_mcp_authorizations_target_cancellation_request_id",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "target_order_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "orders.id",
                    name="fk_mcp_authorizations_target_order_id",
                ),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_mcp_authorizations_action_target_req",
            ["action", "target_cancellation_request_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("mcp_authorizations") as batch_op:
        batch_op.drop_index("ix_mcp_authorizations_action_target_req")
        batch_op.drop_column("target_order_id")
        batch_op.drop_column("target_cancellation_request_id")
        batch_op.alter_column(
            "target_draft_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )

    op.drop_index(
        "ix_cancellation_requests_order_status",
        table_name="cancellation_requests",
    )
    op.drop_table("cancellation_requests")
