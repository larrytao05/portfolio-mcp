from collections.abc import Sequence

from alembic import op

revision: str = "20260927_0015"
down_revision: str | None = "20260926_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE schwab_account_mappings SET masked_account_number = 'Unavailable'"
    )


def downgrade() -> None:
    pass
