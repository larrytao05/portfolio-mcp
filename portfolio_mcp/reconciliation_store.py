from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    func,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from portfolio_mcp.database import Database
from portfolio_mcp.execution import FillSummary, OrderState, require_transition
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventCode,
    OrderEventType,
    OrderStatusSource,
    require_aware_utc,
)
from portfolio_mcp.order_query_store import _epoch_microseconds
from portfolio_mcp.order_record_ops import (
    _append_order_event,
    _invalidate_cancellation_requests_for_order_in_session,
)
from portfolio_mcp.schema import (
    OrderDraftRecord,
    OrderEventRecord,
    OrderRecord,
    ReconciliationGateRecord,
)
from portfolio_mcp.stored_orders import (
    ConcurrentOrderUpdate,
    StoredOrder,
    _order_event_code,
    _stored_order,
    _validate_fill,
)


@dataclass(frozen=True)
class OrderRefreshPlan:
    provider: str
    account_id: str
    target_order_id: str
    next_refresh_at: datetime


@dataclass(frozen=True)
class OrderReconciliationDecision:
    claim: "ReconciliationClaim | None"
    status: str
    next_refresh_at: datetime
    target_order_id: str | None


@dataclass(frozen=True)
class ReconciliationClaim:
    order: StoredOrder
    attempt_id: UUID


def order_refresh_plans(
    db: Database,
    groups: tuple[tuple[str, str], ...],
    *,
    now: datetime,
    minimum_interval: timedelta,
) -> tuple[OrderRefreshPlan, ...]:
    now = require_aware_utc(now)
    if minimum_interval < timedelta(seconds=30):
        raise ValueError("Reconciliation interval must be at least 30 seconds")
    normalized_groups = tuple(sorted(set(groups)))
    if not normalized_groups:
        return ()
    plans: list[OrderRefreshPlan] = []
    with db.sessions() as session:
        for provider, account_id in normalized_groups:
            target_id = _reconciliation_target(session, provider, account_id)
            if target_id is None:
                continue
            next_at = _reconciliation_gate_next_at(
                session, provider, account_id, now, minimum_interval
            )
            plans.append(OrderRefreshPlan(provider, account_id, target_id, next_at))
    return tuple(plans)


def next_order_reconciliation_at(
    db: Database,
    provider: str,
    account_id: str,
    *,
    now: datetime,
    minimum_interval: timedelta,
) -> datetime:
    now = require_aware_utc(now)
    with db.sessions() as session:
        return _reconciliation_gate_next_at(
            session, provider, account_id, now, minimum_interval
        )


def claim_planned_order_reconciliation(
    db: Database,
    order_id: str,
    *,
    attempt_at: datetime,
    minimum_interval: timedelta,
    expected_target_id: str | None = None,
) -> OrderReconciliationDecision:
    attempt_at = require_aware_utc(attempt_at)
    if minimum_interval < timedelta(seconds=30):
        raise ValueError("Reconciliation interval must be at least 30 seconds")
    expected = expected_target_id if expected_target_id is not None else order_id
    with db.sessions.begin() as session:
        record = session.get(OrderRecord, order_id)
        if record is None:
            raise ValueError("Order not found")
        target_id = _reconciliation_target(session, record.provider, record.account_id)
        next_at = _reconciliation_gate_next_at(
            session,
            record.provider,
            record.account_id,
            attempt_at,
            minimum_interval,
        )
        if target_id != expected:
            return OrderReconciliationDecision(
                None, "target_changed", next_at, target_id
            )
        claim = _claim_order_reconciliation(
            session, record, attempt_at, minimum_interval
        )
        if claim is None:
            next_at = _reconciliation_gate_next_at(
                session,
                record.provider,
                record.account_id,
                attempt_at,
                minimum_interval,
            )
            return OrderReconciliationDecision(None, "throttled", next_at, target_id)
        return OrderReconciliationDecision(
            claim, "attempted", attempt_at + minimum_interval, target_id
        )


def claim_order_reconciliation(
    db: Database, order_id: str, *, attempt_at: datetime, minimum_interval: timedelta
) -> "ReconciliationClaim | None":
    attempt_at = require_aware_utc(attempt_at)
    if minimum_interval < timedelta(0):
        raise ValueError("Reconciliation interval must be nonnegative")
    with db.sessions.begin() as session:
        record = session.get(OrderRecord, order_id)
        if record is None:
            raise ValueError("Order not found")
        return _claim_order_reconciliation(
            session, record, attempt_at, minimum_interval
        )


def _claim_order_reconciliation(
    session: Session,
    record: OrderRecord,
    attempt_at: datetime,
    minimum_interval: timedelta,
) -> "ReconciliationClaim | None":
    epoch_us = _epoch_microseconds(attempt_at)
    earliest_next_us = _epoch_microseconds(attempt_at - minimum_interval)
    statement = sqlite_insert(ReconciliationGateRecord).values(
        provider=record.provider,
        account_id=record.account_id,
        last_attempt_at_us=epoch_us,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[
            ReconciliationGateRecord.provider,
            ReconciliationGateRecord.account_id,
        ],
        set_={"last_attempt_at_us": epoch_us},
        where=(ReconciliationGateRecord.last_attempt_at_us <= earliest_next_us),
    )
    claim_result = cast(CursorResult[object], session.execute(statement))
    if claim_result.rowcount != 1:
        return None
    attempt_id = uuid4()
    _append_order_event(
        session,
        draft_id=record.draft_id,
        order_id=record.id,
        account_id=record.account_id,
        event_type=OrderEventType.RECONCILIATION_ATTEMPTED,
        actor=OrderEventActor.SYSTEM,
        occurred_at=attempt_at,
        details={"attempt_id": attempt_id},
        deduplication_key=f"reconciliation:{attempt_id}:attempted",
    )
    return ReconciliationClaim(
        order=_stored_order(record, session.get(OrderDraftRecord, record.draft_id)),
        attempt_id=attempt_id,
    )


def _reconciliation_target(
    session: Session, provider: str, account_id: str
) -> str | None:
    attempted = (
        select(
            OrderEventRecord.order_id.label("order_id"),
            func.max(OrderEventRecord.occurred_at).label("last_attempt_at"),
        )
        .where(
            OrderEventRecord.event_type == OrderEventType.RECONCILIATION_ATTEMPTED.value
        )
        .group_by(OrderEventRecord.order_id)
        .subquery()
    )
    syncable_states = (
        OrderState.ACCEPTED.value,
        OrderState.PARTIALLY_FILLED.value,
        OrderState.CANCEL_PENDING.value,
        OrderState.UNKNOWN.value,
    )
    row = session.execute(
        select(OrderRecord.id)
        .outerjoin(attempted, attempted.c.order_id == OrderRecord.id)
        .where(
            OrderRecord.provider == provider,
            OrderRecord.account_id == account_id,
            OrderRecord.state.in_(syncable_states),
        )
        .order_by(
            attempted.c.last_attempt_at.asc().nulls_first(),
            OrderRecord.created_at.asc(),
            OrderRecord.id.asc(),
        )
        .limit(1)
    ).first()
    return row[0] if row is not None else None


def _reconciliation_gate_next_at(
    session: Session,
    provider: str,
    account_id: str,
    now: datetime,
    minimum_interval: timedelta,
) -> datetime:
    gate = session.scalar(
        select(ReconciliationGateRecord).where(
            ReconciliationGateRecord.provider == provider,
            ReconciliationGateRecord.account_id == account_id,
        )
    )
    if gate is None:
        return now
    last_attempt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        microseconds=gate.last_attempt_at_us
    )
    return max(now, last_attempt + minimum_interval)


def finish_order_reconciliation(
    db: Database,
    order_id: str,
    *,
    attempt_id: UUID,
    expected_version: int,
    expected_state: OrderState,
    state: OrderState,
    now: datetime,
    outcome: str,
    result_code: str,
    result_message: str,
    broker_order_id: str | None = None,
    fill: FillSummary | None = None,
    provider_updated_at: datetime | None = None,
    provider_status_label: str | None = None,
    result_source: OrderStatusSource = OrderStatusSource.SYSTEM,
) -> "StoredOrder":
    now = require_aware_utc(now)
    if provider_updated_at is not None:
        provider_updated_at = require_aware_utc(provider_updated_at)
    allowed_labels = {
        "OPEN",
        "PARTIALLY_FILLED",
        "FILLED",
        "REJECTED",
        "CANCELED",
        "EXPIRED",
    }
    if (
        provider_status_label is not None
        and provider_status_label not in allowed_labels
    ):
        raise ValueError("Unsupported provider status label")
    if outcome not in {
        "matched",
        "not_found",
        "ambiguous",
        "incomplete",
        "mismatch",
        "stale",
        "provider_error",
        "canceled",
        "unknown",
        "refused",
    }:
        raise ValueError("Unsupported reconciliation outcome")
    if result_code != outcome:
        raise ValueError("Reconciliation result code must match its outcome")
    with db.sessions.begin() as session:
        record = session.get(OrderRecord, order_id)
        if record is None:
            raise ValueError("Order not found")
        if (
            record.version != expected_version
            or OrderState(record.state) != expected_state
        ):
            raise ConcurrentOrderUpdate("Order changed during reconciliation")
        previous_state = OrderState(record.state)
        if state != previous_state:
            require_transition(previous_state, state)
        if fill is not None:
            _validate_fill(fill, record.quantity, record.filled_quantity)
        filled_quantity = fill.quantity if fill is not None else record.filled_quantity
        average_fill_price = (
            fill.average_price if fill is not None else record.average_fill_price
        )
        if state == OrderState.FILLED and filled_quantity != record.quantity:
            raise ValueError("Filled quantity must equal the order quantity")
        if state == OrderState.PARTIALLY_FILLED and (
            filled_quantity is not None and filled_quantity >= record.quantity
        ):
            raise ValueError("Partial fill must be below the order quantity")
        updated_at = max(now, record.updated_at)
        values: dict[str, object] = {
            "state": state.value,
            "broker_order_id": broker_order_id or record.broker_order_id,
            "result_code": result_code,
            "result_message": result_message[:256],
            "result_source": result_source.value,
            "filled_quantity": filled_quantity,
            "average_fill_price": average_fill_price,
            "provider_updated_at": provider_updated_at or record.provider_updated_at,
            "provider_status_label": provider_status_label
            or record.provider_status_label,
            "updated_at": updated_at,
            "version": record.version + 1,
        }
        update_result = cast(
            CursorResult[object],
            session.execute(
                update(OrderRecord)
                .where(
                    OrderRecord.id == order_id,
                    OrderRecord.version == expected_version,
                    OrderRecord.state == expected_state,
                )
                .values(**values)
            ),
        )
        if update_result.rowcount != 1:
            raise ConcurrentOrderUpdate("Order changed during reconciliation")
        invalidation_reason = (
            f"order_{state.value.lower()}"
            if state != previous_state
            else "order_state_changed"
        )
        _invalidate_cancellation_requests_for_order_in_session(
            session,
            record.id,
            invalidation_reason,
            updated_at,
            actor=OrderEventActor.SYSTEM,
        )
        result_details: dict[str, object] = {
            "attempt_id": attempt_id,
            "outcome": outcome,
            "status_source": result_source,
        }
        if fill is not None:
            result_details["filled_quantity"] = str(fill.quantity)
            if fill.average_price is not None:
                result_details["average_fill_price"] = str(fill.average_price)
        if provider_updated_at is not None:
            result_details["provider_updated_at"] = provider_updated_at.isoformat()
        if provider_status_label is not None:
            result_details["provider_status_label"] = provider_status_label
        _append_order_event(
            session,
            draft_id=record.draft_id,
            order_id=record.id,
            account_id=record.account_id,
            event_type=OrderEventType.RECONCILIATION_RESULT,
            actor=OrderEventActor.SYSTEM,
            occurred_at=updated_at,
            previous_state=previous_state,
            next_state=state,
            code=OrderEventCode(result_code)
            if result_code in OrderEventCode._value2member_map_
            else OrderEventCode.UNKNOWN,
            details=result_details,
            deduplication_key=f"reconciliation:{attempt_id}:result",
        )
        if state != previous_state:
            details: dict[str, object] = {"status_source": result_source}
            if fill is not None:
                details["filled_quantity"] = str(fill.quantity)
                if fill.average_price is not None:
                    details["average_fill_price"] = str(fill.average_price)
            _append_order_event(
                session,
                draft_id=record.draft_id,
                order_id=record.id,
                account_id=record.account_id,
                event_type=OrderEventType.STATUS_TRANSITION,
                actor=OrderEventActor.SYSTEM,
                occurred_at=updated_at,
                previous_state=previous_state,
                next_state=state,
                code=_order_event_code(result_code, state),
                details=details,
                deduplication_key=(
                    f"order:{record.id}:version:{record.version + 1}:reconciliation"
                ),
            )
        changed = session.get(OrderRecord, order_id)
        assert changed is not None
        return _stored_order(changed, session.get(OrderDraftRecord, changed.draft_id))
