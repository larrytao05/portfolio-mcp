from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261110_0011"
down_revision: str | None = "20261110_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cancellation_attempts",
        sa.Column("account_id", sa.String(128), primary_key=True),
        sa.Column("broker_order_id", sa.String(128), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cancellation_attempts")
