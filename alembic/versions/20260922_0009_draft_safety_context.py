from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260922_0009"
down_revision: str | None = "20260921_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("order_drafts", sa.Column("estimated_notional", sa.Text()))
    op.add_column("order_drafts", sa.Column("account_refreshed_at", sa.String(32)))
    op.add_column("order_drafts", sa.Column("capability_observed_at", sa.String(32)))
    op.add_column(
        "order_drafts", sa.Column("capability_last_success_at", sa.String(32))
    )


def downgrade() -> None:
    op.drop_column("order_drafts", "capability_last_success_at")
    op.drop_column("order_drafts", "capability_observed_at")
    op.drop_column("order_drafts", "account_refreshed_at")
    op.drop_column("order_drafts", "estimated_notional")
