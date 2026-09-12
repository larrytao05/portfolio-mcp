from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260912_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("account_type", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("refreshed_at", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "refresh_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("account_count", sa.Integer(), nullable=False),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(length=128),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("asset_class", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("current_price", sa.Numeric(precision=24, scale=8)),
        sa.Column("market_value", sa.Numeric(precision=24, scale=8)),
        sa.Column("cost_basis", sa.Numeric(precision=24, scale=8)),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.UniqueConstraint("account_id", "symbol"),
    )
    op.create_table(
        "daily_account_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(length=128),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("recorded_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("account_id", "snapshot_date"),
    )


def downgrade() -> None:
    op.drop_table("daily_account_values")
    op.drop_table("positions")
    op.drop_table("refresh_runs")
    op.drop_table("accounts")
