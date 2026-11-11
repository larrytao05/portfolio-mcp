from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260926_0014"
down_revision: str | None = "20260926_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schwab_account_mappings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(length=128),
            sa.ForeignKey("accounts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "schwab_account_hash",
            sa.String(length=128),
            nullable=False,
            unique=True,
        ),
        sa.Column("masked_account_number", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
    )
    op.create_index(
        "ix_schwab_account_mappings_account_id",
        "schwab_account_mappings",
        ["account_id"],
        unique=True,
    )
    op.create_index(
        "ix_schwab_account_mappings_hash",
        "schwab_account_mappings",
        ["schwab_account_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schwab_account_mappings_hash",
        table_name="schwab_account_mappings",
    )
    op.drop_index(
        "ix_schwab_account_mappings_account_id",
        table_name="schwab_account_mappings",
    )
    op.drop_table("schwab_account_mappings")
