from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260912_0003"
down_revision: str | None = "20260912_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "refresh_provider_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "refresh_id",
            sa.Integer(),
            sa.ForeignKey("refresh_runs.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("accounts_refreshed", sa.Integer(), nullable=False),
        sa.Column("stale_accounts", sa.Integer(), nullable=False),
        sa.Column("excluded_accounts", sa.Integer(), nullable=False),
        sa.Column("warning", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("refresh_provider_outcomes")
    op.drop_column("accounts", "is_stale")
