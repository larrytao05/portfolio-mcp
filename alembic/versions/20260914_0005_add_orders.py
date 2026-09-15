from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260914_0005"
down_revision: str | None = "20260912_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_drafts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("account_label", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("instrument_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("instrument_name", sa.String(length=256), nullable=False),
        sa.Column("asset_class", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("order_type", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("limit_price", sa.Text()),
        sa.Column("quote_observed_at", sa.String(length=32)),
        sa.Column("quote_last_price", sa.Text()),
        sa.Column("quote_bid_price", sa.Text()),
        sa.Column("quote_ask_price", sa.Text()),
        sa.Column("quote_source", sa.String(length=64)),
        sa.Column("warnings", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(length=36),
            sa.ForeignKey("order_drafts.id"),
            nullable=False,
        ),
        sa.Column("client_order_id", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("account_label", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("instrument_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("order_type", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("limit_price", sa.Text()),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("broker_order_id", sa.String(length=128)),
        sa.Column("result_code", sa.String(length=64)),
        sa.Column("result_message", sa.String(length=256)),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("draft_id"),
        sa.UniqueConstraint("client_order_id"),
    )
    op.create_table(
        "order_authorizations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(length=36),
            sa.ForeignKey("order_drafts.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("expected_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.Column("consumed_at", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("order_authorizations")
    op.drop_table("orders")
    op.drop_table("order_drafts")
