from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260921_0007"
down_revision: str | None = "20260921_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_capabilities",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("account_capabilities", "is_current")
