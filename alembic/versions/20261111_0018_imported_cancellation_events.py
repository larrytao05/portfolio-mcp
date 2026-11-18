from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261111_0018"
down_revision: str = "20261111_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "imported_cancellation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("broker_order_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id", "broker_order_id"],
            [
                "cancellation_attempts.account_id",
                "cancellation_attempts.broker_order_id",
            ],
        ),
    )

    op.execute(
        "CREATE TRIGGER imported_cancellation_events_no_update "
        "BEFORE UPDATE ON imported_cancellation_events BEGIN "
        "SELECT RAISE(ABORT, 'imported cancellation events are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER imported_cancellation_events_no_delete "
        "BEFORE DELETE ON imported_cancellation_events BEGIN "
        "SELECT RAISE(ABORT, 'imported cancellation events are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER imported_cancellation_events_validate_refs "
        "BEFORE INSERT ON imported_cancellation_events WHEN NOT EXISTS ("
        "SELECT 1 FROM cancellation_attempts WHERE account_id = NEW.account_id "
        "AND broker_order_id = NEW.broker_order_id) BEGIN "
        "SELECT RAISE(ABORT, 'invalid cancellation attempt reference'); END"
    )
    op.execute(
        "CREATE TRIGGER cancellation_attempts_restrict_identity_change "
        "BEFORE UPDATE OF account_id, broker_order_id ON cancellation_attempts "
        "WHEN EXISTS (SELECT 1 FROM imported_cancellation_events "
        "WHERE account_id = OLD.account_id AND broker_order_id = OLD.broker_order_id) "
        "BEGIN SELECT RAISE(ABORT, 'cancellation attempt has append-only events'); END"
    )
    op.execute(
        "CREATE TRIGGER cancellation_attempts_restrict_delete "
        "BEFORE DELETE ON cancellation_attempts WHEN EXISTS ("
        "SELECT 1 FROM imported_cancellation_events WHERE account_id = OLD.account_id "
        "AND broker_order_id = OLD.broker_order_id) BEGIN "
        "SELECT RAISE(ABORT, 'cancellation attempt has append-only events'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS cancellation_attempts_restrict_delete")
    op.execute("DROP TRIGGER IF EXISTS cancellation_attempts_restrict_identity_change")
    op.execute("DROP TRIGGER IF EXISTS imported_cancellation_events_validate_refs")
    op.execute("DROP TRIGGER IF EXISTS imported_cancellation_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS imported_cancellation_events_no_update")
    op.drop_table("imported_cancellation_events")
