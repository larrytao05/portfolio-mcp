from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260921_0006"
down_revision: str | None = "20260914_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_capabilities",
        sa.Column(
            "account_id",
            sa.String(length=128),
            sa.ForeignKey("accounts.id"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("asset_classes", sa.Text(), nullable=False),
        sa.Column("supported_sides", sa.Text(), nullable=False),
        sa.Column("order_types", sa.Text(), nullable=False),
        sa.Column("time_in_force", sa.Text(), nullable=False),
        sa.Column("sizing_modes", sa.Text(), nullable=False),
        sa.Column("preview_supported", sa.Boolean(), nullable=False),
        sa.Column("cancellation_supported", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.String(length=32)),
        sa.Column("last_success_at", sa.String(length=32)),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("blocks", sa.Text(), nullable=False),
        sa.Column("is_stale", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("account_capabilities")
