from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260912_0003"
down_revision: str | None = "20260912_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(length=128),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("provider_transaction_id", sa.String(length=128), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("occurred_at", sa.String(length=32)),
        sa.Column("transaction_type", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32)),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("quantity", sa.Text()),
        sa.Column("amount", sa.Text(), nullable=False),
        sa.Column("fees", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("imported_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("account_id", "provider_transaction_id"),
    )


def downgrade() -> None:
    op.drop_table("activities")
