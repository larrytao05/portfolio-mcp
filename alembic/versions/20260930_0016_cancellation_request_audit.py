from collections.abc import Sequence

from alembic import op

revision: str = "20260930_0016"
down_revision: str | None = "20260927_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EVENT_TYPES = (
    "'draft_created', 'draft_expired', 'authorization_created', "
    "'authorization_consumed', 'authorization_failed', 'submission_started', "
    "'submission_result', 'status_transition', 'reconciliation_attempted', "
    "'reconciliation_result', 'cancellation_requested', 'cancellation_result', "
    "'cancellation_request_created', 'cancellation_request_invalidated'"
)


def upgrade() -> None:
    _drop_order_event_triggers()
    with op.batch_alter_table("order_events", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_order_events_type", type_="check")
        batch_op.create_check_constraint(
            "ck_order_events_type", f"event_type IN ({_EVENT_TYPES})"
        )

    _create_order_event_triggers()


def downgrade() -> None:
    _drop_order_event_triggers()

    with op.batch_alter_table("order_events", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_order_events_type", type_="check")
        batch_op.create_check_constraint(
            "ck_order_events_type",
            "event_type IN ("
            "'draft_created', 'draft_expired', 'authorization_created', "
            "'authorization_consumed', 'authorization_failed', 'submission_started', "
            "'submission_result', 'status_transition', 'reconciliation_attempted', "
            "'reconciliation_result', 'cancellation_requested', 'cancellation_result')",
        )

    _create_order_event_triggers()


def _drop_order_event_triggers() -> None:
    for trigger in (
        "order_events_restrict_order_delete",
        "order_events_restrict_draft_delete",
        "order_events_validate_refs",
        "order_events_no_delete",
        "order_events_no_update",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")


def _create_order_event_triggers() -> None:
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
