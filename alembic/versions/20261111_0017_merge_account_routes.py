from collections.abc import Sequence

revision: str = "20261111_0017"
down_revision: tuple[str, str] = ("20260930_0016", "20261110_0011")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
