import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    select,
    update,
)
from sqlalchemy.engine import CursorResult

from portfolio_mcp.authorization_records import (
    McpAuthorizationError,
    StoredCancellationRequest,
    _stored_cancellation_request,
    compute_cancellation_fingerprint,
)
from portfolio_mcp.database import Database
from portfolio_mcp.execution import FillSummary, OrderState, require_transition
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventType,
    OrderStatusSource,
    decode_event_details,
    require_aware_utc,
)
from portfolio_mcp.order_record_ops import (
    _append_order_event,
    _append_status_transition_event,
    _invalidate_cancellation_requests_for_order_in_session,
)
from portfolio_mcp.schema import (
    CancellationRequestRecord,
    McpAuthorizationRecord,
    OrderAuthorizationRecord,
    OrderDraftRecord,
    OrderEventRecord,
    OrderRecord,
)
from portfolio_mcp.stored_orders import (
    ConcurrentOrderUpdate,
    StoredOrder,
    _order_event_code,
    _stored_order,
    _validate_fill,
)

CancellationKind = Literal["canceled", "refused", "unknown"]


@dataclass(frozen=True)
class CancellationObservation:
    kind: Literal["canceled", "refused", "unknown"]
    observed_order_state: OrderState | None = None
    fill: FillSummary | None = None


_CANCELLATION_METADATA: dict[
    CancellationKind, tuple[str, str, str, OrderStatusSource]
] = {
    "canceled": (
        "canceled",
        "canceled",
        "Order canceled successfully.",
        OrderStatusSource.PROVIDER,
    ),
    "refused": (
        "refused",
        "cancel_rejected",
        "Cancellation was rejected by the broker.",
        OrderStatusSource.PROVIDER,
    ),
    "unknown": (
        "unknown",
        "unknown",
        "Cancellation outcome is unknown. Reconciliation is required.",
        OrderStatusSource.SYSTEM,
    ),
}


def _resolve_cancellation_state(
    record_quantity: Decimal,
    record_filled_quantity: Decimal | None,
    observation: CancellationObservation,
) -> tuple[OrderState, FillSummary | None, CancellationKind]:
    if observation.kind not in ("canceled", "refused"):
        return OrderState.UNKNOWN, None, "unknown"

    fill = observation.fill
    if fill is not None:
        try:
            _validate_fill(fill, record_quantity, record_filled_quantity)
        except ValueError:
            return OrderState.UNKNOWN, None, "unknown"

    cumulative_fill = fill.quantity if fill is not None else record_filled_quantity

    if observation.kind == "canceled":
        if cumulative_fill is not None and cumulative_fill >= record_quantity:
            return OrderState.UNKNOWN, fill, "unknown"
        return OrderState.CANCELED, fill, "canceled"

    obs_state = observation.observed_order_state
    if obs_state == OrderState.FILLED:
        if fill is not None and fill.quantity == record_quantity:
            return OrderState.FILLED, fill, "refused"
    elif obs_state == OrderState.PARTIALLY_FILLED:
        if cumulative_fill is not None and 0 < cumulative_fill < record_quantity:
            return OrderState.PARTIALLY_FILLED, fill, "refused"
    elif obs_state == OrderState.ACCEPTED:
        if cumulative_fill in (None, 0):
            return OrderState.ACCEPTED, fill, "refused"
    elif obs_state == OrderState.EXPIRED:
        if fill is None or fill.quantity < record_quantity:
            return OrderState.EXPIRED, fill, "refused"

    return OrderState.UNKNOWN, fill, "unknown"


def create_cancellation_request(
    db: Database,
    *,
    order: "StoredOrder",
    now: datetime,
    expires_at: datetime,
) -> StoredCancellationRequest:
    req_id = str(uuid4())
    remaining = (
        order.remaining_quantity
        if order.remaining_quantity is not None
        else order.quantity
    )
    fingerprint = compute_cancellation_fingerprint(
        order_id=order.id,
        expected_version=order.version,
        expected_state=order.state.value,
        account_id=order.account_id,
        remaining_quantity=remaining,
    )
    with db.sessions.begin() as session:
        current_version = cast(
            CursorResult[object],
            session.execute(
                update(OrderRecord)
                .where(
                    OrderRecord.id == order.id,
                    OrderRecord.version == order.version,
                    OrderRecord.state == order.state.value,
                    OrderRecord.account_id == order.account_id,
                )
                .values(version=OrderRecord.version)
            ),
        )
        if current_version.rowcount != 1:
            raise ConcurrentOrderUpdate("Order changed while preparing cancellation")
        current = session.get(OrderRecord, order.id)
        if (
            current is None
            or current.version != order.version
            or current.state != order.state.value
            or current.account_id != order.account_id
        ):
            raise ConcurrentOrderUpdate("Order changed while preparing cancellation")
        current_draft = session.get(OrderDraftRecord, current.draft_id)
        current_order = _stored_order(current, current_draft)
        if not current_order.can_cancel:
            raise ValueError(
                current_order.blocking_reason or "Order cannot be canceled"
            )
        _invalidate_cancellation_requests_for_order_in_session(
            session, order.id, "superseded", now, actor=OrderEventActor.MCP
        )
        record = CancellationRequestRecord(
            id=req_id,
            order_id=order.id,
            expected_order_version=order.version,
            expected_order_state=order.state.value,
            account_id=order.account_id,
            provider=order.provider,
            symbol=order.symbol,
            broker_order_id=order.broker_order_id,
            remaining_quantity=remaining,
            action_fingerprint=fingerprint,
            created_at=now,
            expires_at=expires_at,
            status="pending",
            invalidation_reason=None,
        )
        session.add(record)
        session.flush()
        _append_order_event(
            session,
            draft_id=current.draft_id,
            order_id=current.id,
            account_id=current.account_id,
            event_type=OrderEventType.CANCELLATION_REQUEST_CREATED,
            actor=OrderEventActor.MCP,
            occurred_at=now,
            details={"request_id": req_id, "expires_at": expires_at.isoformat()},
            deduplication_key=f"cancellation_request:{req_id}:created",
        )
        return _stored_cancellation_request(record)


def invalidate_cancellation_requests_for_order(
    db: Database, order_id: str, reason: str = "order_state_changed"
) -> None:
    now = require_aware_utc(db.clock())
    with db.sessions.begin() as session:
        _invalidate_cancellation_requests_for_order_in_session(
            session, order_id, reason, now
        )


def authorize_cancellation_request(
    db: Database,
    request_id: str,
    *,
    expected_fingerprint: str,
    authorization_id: str,
    salt_hex: str,
    digest_hex: str,
    now: datetime,
    expires_at: datetime,
) -> tuple[StoredCancellationRequest, str]:
    now = require_aware_utc(now)
    expires_at = require_aware_utc(expires_at)
    with db.sessions() as session:
        req = session.get(CancellationRequestRecord, request_id)
        if req is None:
            raise McpAuthorizationError(
                "cancellation_request_not_found", "Cancellation request not found"
            )
        if req.status != "pending":
            raise McpAuthorizationError(
                "cancellation_request_not_pending",
                f"Cancellation request is already {req.status}",
            )
        if now > req.expires_at:
            _invalidate_cancellation_requests_for_order_in_session(
                session,
                req.order_id,
                "expired",
                now,
                actor=OrderEventActor.MCP,
                request_id=req.id,
            )
            session.commit()
            raise McpAuthorizationError(
                "cancellation_request_expired",
                "Cancellation request has expired",
            )
        if req.action_fingerprint != expected_fingerprint:
            raise McpAuthorizationError(
                "fingerprint_mismatch",
                "The reviewed cancellation request no longer matches",
            )

        order = session.get(OrderRecord, req.order_id)
        draft = (
            session.get(OrderDraftRecord, order.draft_id) if order is not None else None
        )
        if (
            order is None
            or order.version != req.expected_order_version
            or order.state != req.expected_order_state
        ):
            _invalidate_cancellation_requests_for_order_in_session(
                session,
                req.order_id,
                "order_state_changed",
                now,
                actor=OrderEventActor.DASHBOARD,
            )
            session.commit()
            raise McpAuthorizationError(
                "order_state_changed",
                "The order state or version has changed since the "
                "cancellation request was created",
            )

        stored = _stored_order(order, draft)
        if not stored.can_cancel:
            _invalidate_cancellation_requests_for_order_in_session(
                session,
                req.order_id,
                "order_not_cancelable",
                now,
                actor=OrderEventActor.DASHBOARD,
            )
            session.commit()
            raise McpAuthorizationError(
                "order_not_cancelable",
                stored.blocking_reason or "Order cannot be canceled",
            )

        req.status = "authorized"
        session.execute(
            update(McpAuthorizationRecord)
            .where(
                McpAuthorizationRecord.target_cancellation_request_id == req.id,
                McpAuthorizationRecord.consumed_at.is_(None),
                McpAuthorizationRecord.invalidation_reason.is_(None),
            )
            .values(invalidation_reason="superseded")
        )

        auth_record = McpAuthorizationRecord(
            id=authorization_id,
            action="cancel",
            target_draft_id=None,
            target_cancellation_request_id=req.id,
            target_order_id=order.id,
            payload_fingerprint=req.action_fingerprint,
            account_id=order.account_id,
            salt=salt_hex,
            digest=digest_hex,
            created_at=now,
            expires_at=expires_at,
            consumed_at=None,
            invalidation_reason=None,
            failed_attempts=0,
        )
        session.add(auth_record)

        _append_order_event(
            session,
            draft_id=order.draft_id,
            order_id=order.id,
            account_id=order.account_id,
            event_type=OrderEventType.AUTHORIZATION_CREATED,
            actor=OrderEventActor.DASHBOARD,
            occurred_at=now,
            previous_state=None,
            next_state=None,
            code=None,
            details={
                "authorization_id": authorization_id,
                "action": "cancel",
            },
            deduplication_key=f"order:{order.id}:auth:cancel:{authorization_id}",
        )
        session.commit()
        return _stored_cancellation_request(req), order.id


def get_cancellation_request(
    db: Database, request_id: str
) -> StoredCancellationRequest | None:
    with db.sessions.begin() as session:
        record = session.get(CancellationRequestRecord, request_id)
        if record is None:
            return None
        now = require_aware_utc(db.clock())
        if record.status in {"pending", "authorized"} and now > record.expires_at:
            _invalidate_cancellation_requests_for_order_in_session(
                session,
                record.order_id,
                "expired",
                now,
                actor=OrderEventActor.SYSTEM,
                request_id=record.id,
            )
        return _stored_cancellation_request(record)


def active_cancellation_request_for_order(
    db: Database, order_id: str
) -> StoredCancellationRequest | None:
    with db.sessions.begin() as session:
        now = require_aware_utc(db.clock())
        record = session.scalars(
            select(CancellationRequestRecord)
            .where(
                CancellationRequestRecord.order_id == order_id,
                CancellationRequestRecord.status.in_(["pending", "authorized"]),
                CancellationRequestRecord.expires_at > now,
            )
            .order_by(CancellationRequestRecord.created_at.desc())
        ).first()
        if record is None:
            return None
        return _stored_cancellation_request(record)


def update_cancellation_request_status(
    db: Database, request_id: str, status: str
) -> None:
    with db.sessions.begin() as session:
        if status == "expired":
            request = session.get(CancellationRequestRecord, request_id)
            if request is not None and request.status in {"pending", "authorized"}:
                _invalidate_cancellation_requests_for_order_in_session(
                    session,
                    request.order_id,
                    "expired",
                    require_aware_utc(db.clock()),
                    actor=OrderEventActor.MCP,
                    request_id=request.id,
                )
            return
        session.execute(
            update(CancellationRequestRecord)
            .where(CancellationRequestRecord.id == request_id)
            .values(status=status)
        )


def begin_order_cancellation(
    db: Database,
    order_id: str,
    *,
    expected_version: int,
    expected_state: OrderState,
    now: datetime,
    actor: OrderEventActor = OrderEventActor.DASHBOARD,
) -> tuple["StoredOrder", UUID | None, bool]:
    now = require_aware_utc(now)
    with db.sessions.begin() as session:
        record = session.get(OrderRecord, order_id)
        if record is None:
            raise ValueError("Order not found")

        draft_record = session.get(OrderDraftRecord, record.draft_id)
        if record.state == OrderState.CANCELED.value:
            return _stored_order(record, draft_record), None, False

        if record.state == OrderState.CANCEL_PENDING.value:
            attempt_event = session.scalars(
                select(OrderEventRecord)
                .where(
                    OrderEventRecord.order_id == order_id,
                    OrderEventRecord.event_type
                    == OrderEventType.CANCELLATION_REQUESTED.value,
                )
                .order_by(OrderEventRecord.occurred_at.desc())
            ).first()
            attempt_id = (
                UUID(decode_event_details(attempt_event.details_json)["attempt_id"])
                if attempt_event is not None
                else uuid4()
            )
            return _stored_order(record, draft_record), attempt_id, False

        if (
            record.version != expected_version
            or OrderState(record.state) != expected_state
        ):
            raise ConcurrentOrderUpdate("Order changed before cancellation")

        stored = _stored_order(record, draft_record)
        if not stored.can_cancel:
            raise ValueError(stored.blocking_reason or "Order cannot be canceled")

        attempt_id = uuid4()
        unfilled = (
            str(record.quantity - record.filled_quantity)
            if record.filled_quantity is not None
            else str(record.quantity)
        )
        cancellation_payload = {
            "order_id": record.id,
            "version": record.version,
            "state": record.state,
            "account_id": record.account_id,
            "broker_order_id": record.broker_order_id or "",
            "action": "cancel",
            "unfilled_remainder": unfilled,
        }
        cancellation_fingerprint = hashlib.sha256(
            json.dumps(
                cancellation_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

        event_occurred_at = max(now, record.updated_at)
        claim_stmt = (
            update(OrderRecord)
            .where(
                OrderRecord.id == order_id,
                OrderRecord.version == expected_version,
                OrderRecord.state == expected_state.value,
            )
            .values(
                state=OrderState.CANCEL_PENDING.value,
                version=OrderRecord.version + 1,
                updated_at=event_occurred_at,
            )
        )
        claim_res = cast(CursorResult[object], session.execute(claim_stmt))
        if claim_res.rowcount != 1:
            session.expire_all()
            current = session.get(OrderRecord, order_id)
            if current is not None and current.state == OrderState.CANCEL_PENDING.value:
                attempt_event = session.scalars(
                    select(OrderEventRecord)
                    .where(
                        OrderEventRecord.order_id == order_id,
                        OrderEventRecord.event_type
                        == OrderEventType.CANCELLATION_REQUESTED.value,
                    )
                    .order_by(OrderEventRecord.occurred_at.desc())
                ).first()
                attempt_id = (
                    UUID(decode_event_details(attempt_event.details_json)["attempt_id"])
                    if attempt_event is not None
                    else uuid4()
                )
                return (
                    _stored_order(
                        current, session.get(OrderDraftRecord, current.draft_id)
                    ),
                    attempt_id,
                    False,
                )
            raise ConcurrentOrderUpdate("Order changed before cancellation")

        session.refresh(record)

        if actor == OrderEventActor.DASHBOARD:
            session.add(
                OrderAuthorizationRecord(
                    id=str(attempt_id),
                    draft_id=record.draft_id,
                    action="cancel",
                    expected_fingerprint=cancellation_fingerprint,
                    account_id=record.account_id,
                    actor="dashboard-owner",
                    created_at=now,
                    expires_at=now + timedelta(minutes=5),
                    consumed_at=now,
                )
            )

            auth_details = {
                "authorization_id": str(attempt_id),
                "action": "cancel",
            }
            for event_type in (
                OrderEventType.AUTHORIZATION_CREATED,
                OrderEventType.AUTHORIZATION_CONSUMED,
            ):
                _append_order_event(
                    session,
                    draft_id=record.draft_id,
                    order_id=record.id,
                    account_id=record.account_id,
                    event_type=event_type,
                    actor=actor,
                    occurred_at=now,
                    details=auth_details,
                    deduplication_key=(
                        f"authorization:{attempt_id}:"
                        f"{event_type.value.removeprefix('authorization_')}"
                    ),
                )

        _append_order_event(
            session,
            draft_id=record.draft_id,
            order_id=record.id,
            account_id=record.account_id,
            event_type=OrderEventType.CANCELLATION_REQUESTED,
            actor=actor,
            occurred_at=now,
            details={"attempt_id": str(attempt_id)},
            deduplication_key=f"order:{record.id}:cancel:{attempt_id}:requested",
        )

        _invalidate_cancellation_requests_for_order_in_session(
            session,
            record.id,
            "cancellation_started",
            event_occurred_at,
            actor=actor,
        )
        session.flush()

        _append_status_transition_event(
            session,
            record=record,
            previous_state=expected_state,
            next_state=OrderState.CANCEL_PENDING,
            actor=actor,
            occurred_at=event_occurred_at,
            status_source=OrderStatusSource.LOCAL,
        )

        return _stored_order(record, draft_record), attempt_id, True


def finish_order_cancellation(
    db: Database,
    order_id: str,
    *,
    attempt_id: UUID,
    observation: CancellationObservation,
    now: datetime,
    actor: OrderEventActor = OrderEventActor.SYSTEM,
) -> "StoredOrder":
    now = require_aware_utc(now)
    with db.sessions.begin() as session:
        record = session.get(OrderRecord, order_id)
        if record is None:
            raise ValueError("Order not found")

        draft_record = session.get(OrderDraftRecord, record.draft_id)
        if record.state != OrderState.CANCEL_PENDING.value:
            return _stored_order(record, draft_record)

        attempt_event = session.scalars(
            select(OrderEventRecord)
            .where(
                OrderEventRecord.order_id == order_id,
                OrderEventRecord.event_type
                == OrderEventType.CANCELLATION_REQUESTED.value,
            )
            .order_by(OrderEventRecord.occurred_at.desc())
        ).first()
        if attempt_event is not None:
            details = decode_event_details(attempt_event.details_json)
            active_attempt_str = details.get("attempt_id")
            if active_attempt_str and str(attempt_id) != active_attempt_str:
                return _stored_order(record, draft_record)

        target_state, applied_fill, effective_kind = _resolve_cancellation_state(
            record.quantity, record.filled_quantity, observation
        )
        outcome, result_code, result_message, result_source = _CANCELLATION_METADATA[
            effective_kind
        ]

        previous_state = OrderState(record.state)
        require_transition(previous_state, target_state)

        if applied_fill is not None:
            record.filled_quantity = applied_fill.quantity
            record.average_fill_price = applied_fill.average_price

        record.state = target_state.value
        record.result_code = result_code
        record.result_message = result_message[:256]
        record.result_source = result_source.value
        event_occurred_at = max(now, record.updated_at)
        record.updated_at = event_occurred_at
        record.version += 1
        _invalidate_cancellation_requests_for_order_in_session(
            session,
            record.id,
            f"order_{target_state.value.lower()}",
            event_occurred_at,
            actor=actor,
        )
        session.flush()

        _append_order_event(
            session,
            draft_id=record.draft_id,
            order_id=record.id,
            account_id=record.account_id,
            event_type=OrderEventType.CANCELLATION_RESULT,
            actor=actor,
            occurred_at=event_occurred_at,
            previous_state=previous_state,
            next_state=target_state,
            code=_order_event_code(result_code, target_state),
            details={"attempt_id": str(attempt_id), "outcome": outcome},
            deduplication_key=f"order:{record.id}:cancel:{attempt_id}:result",
        )

        _append_status_transition_event(
            session,
            record=record,
            previous_state=previous_state,
            next_state=target_state,
            actor=actor,
            occurred_at=event_occurred_at,
            status_source=result_source,
            fill=applied_fill,
        )

        return _stored_order(record, draft_record)
