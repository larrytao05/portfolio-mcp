from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260925_0010"
down_revision: str | None = "20260922_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("filled_quantity", sa.Text()))
    op.add_column("orders", sa.Column("average_fill_price", sa.Text()))
    op.add_column("orders", sa.Column("result_source", sa.String(length=16)))
    op.create_index("ix_orders_updated_at_id", "orders", ["updated_at", "id"])
    op.create_index(
        "ix_orders_account_updated_at_id",
        "orders",
        ["account_id", "updated_at", "id"],
    )
    op.create_table(
        "order_events",
        sa.Column("event_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(length=36),
            sa.ForeignKey("order_drafts.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "order_id",
            sa.String(length=36),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
        ),
        sa.Column("account_id", sa.String(length=128)),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("previous_state", sa.String(length=32)),
        sa.Column("next_state", sa.String(length=32)),
        sa.Column("code", sa.String(length=64)),
        sa.Column("details_schema_version", sa.Integer(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("deduplication_key", sa.String(length=160), nullable=False),
        sa.Column("occurred_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("deduplication_key"),
        sa.CheckConstraint(
            "actor IN ('dashboard', 'mcp', 'system')", name="ck_order_events_actor"
        ),
        sa.CheckConstraint(
            "event_type IN ('draft_created', 'draft_expired', "
            "'authorization_created', 'authorization_consumed', "
            "'authorization_failed', 'submission_started', "
            "'submission_result', 'status_transition', "
            "'reconciliation_attempted', 'reconciliation_result', "
            "'cancellation_requested', 'cancellation_result')",
            name="ck_order_events_type",
        ),
    )
    op.create_index(
        "ix_order_events_occurred_at_event_id",
        "order_events",
        ["occurred_at", "event_id"],
    )
    op.create_index(
        "ix_order_events_draft_occurred_at_event_id",
        "order_events",
        ["draft_id", "occurred_at", "event_id"],
    )
    op.create_index(
        "ix_order_events_order_occurred_at_event_id",
        "order_events",
        ["order_id", "occurred_at", "event_id"],
    )
    op.create_index(
        "ix_order_events_account_occurred_at_event_id",
        "order_events",
        ["account_id", "occurred_at", "event_id"],
    )
    op.execute(
        "CREATE TRIGGER order_events_no_update "
        "BEFORE UPDATE ON order_events BEGIN "
        "SELECT RAISE(ABORT, 'order_events are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER order_events_no_delete "
        "BEFORE DELETE ON order_events BEGIN "
        "SELECT RAISE(ABORT, 'order_events are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER order_events_validate_refs BEFORE INSERT ON order_events "
        "BEGIN "
        "SELECT RAISE(ABORT, 'invalid order event draft reference') "
        "WHERE NEW.draft_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM order_drafts WHERE id = NEW.draft_id); "
        "SELECT RAISE(ABORT, 'invalid order event account reference') "
        "WHERE NEW.draft_id IS NOT NULL AND NEW.account_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM order_drafts WHERE id = NEW.draft_id "
        "AND account_id = NEW.account_id); "
        "SELECT RAISE(ABORT, 'invalid order event order reference') "
        "WHERE NEW.order_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM orders WHERE id = NEW.order_id "
        "AND (NEW.draft_id IS NULL OR draft_id = NEW.draft_id) "
        "AND (NEW.account_id IS NULL OR account_id = NEW.account_id)); "
        "END"
    )
    op.execute(
        "CREATE TRIGGER order_events_restrict_draft_delete "
        "BEFORE DELETE ON order_drafts WHEN EXISTS ("
        "SELECT 1 FROM order_events WHERE draft_id = OLD.id) BEGIN "
        "SELECT RAISE(ABORT, 'draft has append-only order events'); END"
    )
    op.execute(
        "CREATE TRIGGER order_events_restrict_order_delete "
        "BEFORE DELETE ON orders WHEN EXISTS ("
        "SELECT 1 FROM order_events WHERE order_id = OLD.id) BEGIN "
        "SELECT RAISE(ABORT, 'order has append-only order events'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS order_events_restrict_order_delete")
    op.execute("DROP TRIGGER IF EXISTS order_events_restrict_draft_delete")
    op.execute("DROP TRIGGER IF EXISTS order_events_validate_refs")
    op.execute("DROP TRIGGER IF EXISTS order_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS order_events_no_update")
    op.drop_index(
        "ix_order_events_account_occurred_at_event_id", table_name="order_events"
    )
    op.drop_index(
        "ix_order_events_order_occurred_at_event_id", table_name="order_events"
    )
    op.drop_index(
        "ix_order_events_draft_occurred_at_event_id", table_name="order_events"
    )
    op.drop_index("ix_order_events_occurred_at_event_id", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_orders_account_updated_at_id", table_name="orders")
    op.drop_index("ix_orders_updated_at_id", table_name="orders")
    op.drop_column("orders", "result_source")
    op.drop_column("orders", "average_fill_price")
    op.drop_column("orders", "filled_quantity")
