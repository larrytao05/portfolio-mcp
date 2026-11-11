from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260925_0011"
down_revision: str | None = "20260925_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("provider_submission_started_at", sa.String(length=32))
    )
    op.add_column("orders", sa.Column("provider_updated_at", sa.String(length=32)))
    op.add_column("orders", sa.Column("provider_status_label", sa.String(length=32)))
    op.create_table(
        "order_reconciliation_gates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("last_attempt_at_us", sa.Integer(), nullable=False),
        sa.UniqueConstraint("provider", "account_id"),
    )


def downgrade() -> None:
    op.drop_table("order_reconciliation_gates")
    op.drop_column("orders", "provider_status_label")
    op.drop_column("orders", "provider_updated_at")
    op.drop_column("orders", "provider_submission_started_at")
