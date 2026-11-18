from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261110_0010"
down_revision: str | None = "20260922_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("provider_route", sa.String(32), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("credential_reference", sa.String(128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "account_routes",
        sa.Column("account_id", sa.String(128), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(128),
            sa.ForeignKey("connections.id"),
            nullable=False,
        ),
        sa.Column("upstream_account_id", sa.String(128), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("trade_enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("connection_id", "upstream_account_id"),
    )


def downgrade() -> None:
    op.drop_table("account_routes")
    op.drop_table("connections")
