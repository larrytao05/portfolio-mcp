from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260921_0008"
down_revision: str | None = "20260921_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("live_trading_enabled", sa.Boolean(), nullable=False),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False),
        sa.Column("max_order_shares", sa.Text()),
        sa.Column("max_order_notional_usd", sa.Text()),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("trading_settings")
