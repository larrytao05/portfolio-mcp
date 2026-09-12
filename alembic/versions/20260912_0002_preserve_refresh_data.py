from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260912_0002"
down_revision: str | None = "20260912_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("positions") as batch_op:
        for column_name in (
            "quantity",
            "current_price",
            "market_value",
            "cost_basis",
        ):
            batch_op.alter_column(
                column_name,
                existing_type=sa.Numeric(precision=24, scale=8),
                type_=sa.Text(),
            )
    with op.batch_alter_table("daily_account_values") as batch_op:
        batch_op.alter_column(
            "value",
            existing_type=sa.Numeric(precision=24, scale=8),
            type_=sa.Text(),
            existing_nullable=False,
        )
    op.add_column(
        "refresh_runs", sa.Column("error_code", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "refresh_runs",
        sa.Column("error_message", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refresh_runs", "error_message")
    op.drop_column("refresh_runs", "error_code")
    with op.batch_alter_table("daily_account_values") as batch_op:
        batch_op.alter_column(
            "value",
            existing_type=sa.Text(),
            type_=sa.Numeric(precision=24, scale=8),
            existing_nullable=False,
        )
    with op.batch_alter_table("positions") as batch_op:
        for column_name in (
            "quantity",
            "current_price",
            "market_value",
            "cost_basis",
        ):
            batch_op.alter_column(
                column_name,
                existing_type=sa.Text(),
                type_=sa.Numeric(precision=24, scale=8),
            )
