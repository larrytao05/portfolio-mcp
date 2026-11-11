from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    select,
    update,
)
from sqlalchemy.orm import Session

from portfolio_mcp.execution import FillSummary, OrderState
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventCode,
    OrderEventType,
    OrderStatusSource,
    encode_event_details,
    require_aware_utc,
)
from portfolio_mcp.schema import (
    CancellationRequestRecord,
    McpAuthorizationRecord,
    OrderEventRecord,
    OrderRecord,
)


def _append_order_event(
    session: Session,
    *,
    draft_id: str | None,
    order_id: str | None,
    account_id: str | None,
    event_type: OrderEventType,
    actor: OrderEventActor,
    occurred_at: datetime,
    previous_state: OrderState | None = None,
    next_state: OrderState | None = None,
    code: OrderEventCode | None = None,
    details: Mapping[str, object] | None = None,
    deduplication_key: str,
) -> None:
    if not deduplication_key or len(deduplication_key) > 160:
        raise ValueError("Invalid order event deduplication key")
    normalized_time = require_aware_utc(occurred_at)
    session.add(
        OrderEventRecord(
            event_id=str(uuid4()),
            draft_id=draft_id,
            order_id=order_id,
            account_id=account_id,
            event_type=event_type.value,
            actor=actor.value,
            previous_state=previous_state.value if previous_state else None,
            next_state=next_state.value if next_state else None,
            code=code.value if code else None,
            details_schema_version=1,
            details_json=encode_event_details(event_type, details or {}),
            deduplication_key=deduplication_key,
            occurred_at=normalized_time,
        )
    )


def _append_status_transition_event(
    session: Session,
    *,
    record: OrderRecord,
    previous_state: OrderState,
    next_state: OrderState,
    actor: OrderEventActor,
    occurred_at: datetime,
    status_source: OrderStatusSource,
    fill: FillSummary | None = None,
) -> None:
    details: dict[str, object] = {"status_source": status_source}
    filled_qty = fill.quantity if fill is not None else record.filled_quantity
    avg_price = fill.average_price if fill is not None else record.average_fill_price
    if filled_qty is not None:
        details["filled_quantity"] = str(filled_qty)
        if avg_price is not None:
            details["average_fill_price"] = str(avg_price)
    _append_order_event(
        session,
        draft_id=record.draft_id,
        order_id=record.id,
        account_id=record.account_id,
        event_type=OrderEventType.STATUS_TRANSITION,
        actor=actor,
        occurred_at=occurred_at,
        previous_state=previous_state,
        next_state=next_state,
        details=details,
        deduplication_key=(
            f"order:{record.id}:version:{record.version}:status_transition"
        ),
    )


def _invalidate_cancellation_requests_for_order_in_session(
    session: Session,
    order_id: str,
    reason: str,
    now: datetime,
    *,
    actor: OrderEventActor = OrderEventActor.SYSTEM,
    request_id: str | None = None,
) -> None:
    request_query = select(CancellationRequestRecord).where(
        CancellationRequestRecord.order_id == order_id,
        CancellationRequestRecord.status.in_(["pending", "authorized"]),
    )
    if request_id is not None:
        request_query = request_query.where(CancellationRequestRecord.id == request_id)
    requests = session.scalars(request_query).all()
    order = session.get(OrderRecord, order_id)
    for request in requests:
        request.status = "expired" if reason == "expired" else "invalidated"
        request.invalidation_reason = reason
        session.execute(
            update(McpAuthorizationRecord)
            .where(
                McpAuthorizationRecord.target_cancellation_request_id == request.id,
                McpAuthorizationRecord.consumed_at.is_(None),
                McpAuthorizationRecord.invalidation_reason.is_(None),
            )
            .values(invalidation_reason=reason)
        )
        if order is not None:
            _append_order_event(
                session,
                draft_id=order.draft_id,
                order_id=order.id,
                account_id=order.account_id,
                event_type=OrderEventType.CANCELLATION_REQUEST_INVALIDATED,
                actor=actor,
                occurred_at=max(now, order.updated_at),
                details={"request_id": request.id, "reason": reason},
                deduplication_key=f"cancellation_request:{request.id}:invalidated",
            )
