import json
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from portfolio_mcp.database import Database
from portfolio_mcp.execution import FillSummary, OrderState, require_transition
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventCode,
    OrderEventType,
    OrderStatusSource,
    require_aware_utc,
)
from portfolio_mcp.order_record_ops import (
    _append_order_event,
    _invalidate_cancellation_requests_for_order_in_session,
)
from portfolio_mcp.schema import (
    OrderAuthorizationRecord,
    OrderDraftRecord,
    OrderEventRecord,
    OrderRecord,
)
from portfolio_mcp.stored_orders import (
    ConcurrentOrderUpdate,
    OrderDraft,
    StoredOrder,
    _order_event_code,
    _stored_order,
    _validate_fill,
)


def save_order_draft(
    db: Database,
    draft: "OrderDraft",
    actor: OrderEventActor = OrderEventActor.DASHBOARD,
) -> None:
    with db.sessions.begin() as session:
        session.add(
            OrderDraftRecord(
                id=draft.id,
                account_id=draft.account_id,
                account_label=draft.account_label,
                provider=draft.provider,
                instrument_id=draft.instrument_id,
                symbol=draft.symbol,
                instrument_name=draft.instrument_name,
                asset_class=draft.asset_class,
                side=draft.side,
                order_type=draft.order_type,
                quantity=draft.quantity,
                limit_price=draft.limit_price,
                quote_observed_at=draft.quote_observed_at,
                quote_last_price=draft.quote_last_price,
                quote_bid_price=draft.quote_bid_price,
                quote_ask_price=draft.quote_ask_price,
                quote_source=draft.quote_source,
                estimated_notional=draft.estimated_notional,
                account_refreshed_at=draft.account_refreshed_at,
                capability_observed_at=draft.capability_observed_at,
                capability_last_success_at=draft.capability_last_success_at,
                warnings=json.dumps(draft.warnings),
                fingerprint=draft.fingerprint,
                created_at=draft.created_at,
                expires_at=draft.expires_at,
            )
        )
        session.flush()
        _append_order_event(
            session,
            draft_id=draft.id,
            order_id=None,
            account_id=draft.account_id,
            event_type=OrderEventType.DRAFT_CREATED,
            actor=actor,
            occurred_at=draft.created_at,
            details={},
            deduplication_key=f"draft:{draft.id}:created",
        )


def begin_order_submission(
    db: Database,
    draft: "OrderDraft",
    now: datetime,
    actor: OrderEventActor = OrderEventActor.DASHBOARD,
) -> tuple["StoredOrder", bool]:
    now = require_aware_utc(now)
    order_id = str(uuid4())
    client_order_id = str(uuid4())
    try:
        with db.sessions.begin() as session:
            existing = session.scalar(
                select(OrderRecord).where(OrderRecord.draft_id == draft.id)
            )
            if existing is not None:
                return _stored_order(
                    existing, session.get(OrderDraftRecord, existing.draft_id)
                ), False
            authorization_id = str(uuid4())
            session.add(
                OrderAuthorizationRecord(
                    id=authorization_id,
                    draft_id=draft.id,
                    action="submit",
                    expected_fingerprint=draft.fingerprint,
                    account_id=draft.account_id,
                    actor="dashboard-owner"
                    if actor == OrderEventActor.DASHBOARD
                    else actor.value,
                    created_at=now,
                    expires_at=draft.expires_at,
                    consumed_at=now,
                )
            )
            record = OrderRecord(
                id=order_id,
                draft_id=draft.id,
                client_order_id=client_order_id,
                fingerprint=draft.fingerprint,
                account_id=draft.account_id,
                account_label=draft.account_label,
                provider=draft.provider,
                instrument_id=draft.instrument_id,
                symbol=draft.symbol,
                side=draft.side,
                order_type=draft.order_type,
                quantity=draft.quantity,
                limit_price=draft.limit_price,
                state=OrderState.SUBMITTING,
                created_at=now,
                updated_at=now,
                version=1,
            )
            session.add(record)
            session.flush()
            if actor == OrderEventActor.DASHBOARD:
                authorization_details = {
                    "authorization_id": authorization_id,
                    "action": "submit",
                }
                for event_type in (
                    OrderEventType.AUTHORIZATION_CREATED,
                    OrderEventType.AUTHORIZATION_CONSUMED,
                ):
                    _append_order_event(
                        session,
                        draft_id=draft.id,
                        order_id=order_id,
                        account_id=draft.account_id,
                        event_type=event_type,
                        actor=actor,
                        occurred_at=now,
                        details=authorization_details,
                        deduplication_key=(
                            f"authorization:{authorization_id}:"
                            f"{event_type.value.removeprefix('authorization_')}"
                        ),
                    )
            _append_order_event(
                session,
                draft_id=draft.id,
                order_id=order_id,
                account_id=draft.account_id,
                event_type=OrderEventType.SUBMISSION_STARTED,
                actor=actor,
                occurred_at=now,
                next_state=OrderState.SUBMITTING,
                details={},
                deduplication_key=f"order:{order_id}:version:1:started",
            )
            return _stored_order(record, session.get(OrderDraftRecord, draft.id)), True
    except IntegrityError:
        with db.sessions() as session:
            existing = session.scalar(
                select(OrderRecord).where(OrderRecord.draft_id == draft.id)
            )
            if (
                existing is None
                or existing.fingerprint != draft.fingerprint
                or existing.account_id != draft.account_id
                or existing.provider != draft.provider
            ):
                raise
            started_event = session.scalar(
                select(OrderEventRecord.event_id).where(
                    OrderEventRecord.order_id == existing.id,
                    OrderEventRecord.event_type
                    == OrderEventType.SUBMISSION_STARTED.value,
                )
            )
            if started_event is None:
                raise
            return _stored_order(
                existing, session.get(OrderDraftRecord, existing.draft_id)
            ), False


def mark_provider_submission_started(
    db: Database, order_id: str, *, expected_version: int, started_at: datetime
) -> "StoredOrder":
    started_at = require_aware_utc(started_at)
    with db.sessions.begin() as session:
        result = cast(
            CursorResult[object],
            session.execute(
                update(OrderRecord)
                .where(
                    OrderRecord.id == order_id,
                    OrderRecord.version == expected_version,
                    OrderRecord.state == OrderState.SUBMITTING,
                )
                .values(provider_submission_started_at=started_at)
            ),
        )
        if result.rowcount != 1:
            raise ConcurrentOrderUpdate("Order changed before provider submission")
        record = session.get(OrderRecord, order_id)
        assert record is not None
        return _stored_order(record, session.get(OrderDraftRecord, record.draft_id))


def record_draft_expiry(db: Database, draft_id: str, *, observed_at: datetime) -> bool:
    observed_at = require_aware_utc(observed_at)
    with db.sessions.begin() as session:
        draft = session.get(OrderDraftRecord, draft_id)
        if draft is None or observed_at <= draft.expires_at:
            return False
        existing = session.scalar(
            select(OrderEventRecord.event_id).where(
                OrderEventRecord.deduplication_key == f"draft:{draft_id}:expired"
            )
        )
        if existing is not None:
            return False
        _append_order_event(
            session,
            draft_id=draft.id,
            order_id=None,
            account_id=draft.account_id,
            event_type=OrderEventType.DRAFT_EXPIRED,
            actor=OrderEventActor.DASHBOARD,
            occurred_at=observed_at,
            code=OrderEventCode.DRAFT_EXPIRED,
            details={"expires_at": draft.expires_at.isoformat()},
            deduplication_key=f"draft:{draft_id}:expired",
        )
        return True


def record_authorization_failure(
    db: Database,
    *,
    attempt_id: UUID,
    draft_id: str | None,
    action: str,
    actor: OrderEventActor,
    code: OrderEventCode,
    occurred_at: datetime,
) -> bool:
    if action not in {"submit", "cancel"}:
        raise ValueError("Invalid authorization action")
    with db.sessions.begin() as session:
        draft = session.get(OrderDraftRecord, draft_id) if draft_id else None
        dedupe_key = f"authorization:{attempt_id}:failed"
        if (
            session.scalar(
                select(OrderEventRecord.event_id).where(
                    OrderEventRecord.deduplication_key == dedupe_key
                )
            )
            is not None
        ):
            return False
        _append_order_event(
            session,
            draft_id=draft.id if draft is not None else None,
            order_id=None,
            account_id=draft.account_id if draft is not None else None,
            event_type=OrderEventType.AUTHORIZATION_FAILED,
            actor=actor,
            occurred_at=occurred_at,
            code=code,
            details={"attempt_id": attempt_id, "action": action},
            deduplication_key=dedupe_key,
        )
        return True


def recover_stranded_submissions(db: Database, now: datetime) -> int:
    now = require_aware_utc(now)
    with db.sessions() as session:
        candidates = list(
            session.execute(
                select(OrderRecord.id, OrderRecord.draft_id).where(
                    OrderRecord.state == OrderState.SUBMITTING
                )
            )
        )
    recovered = 0
    for order_id, draft_id in candidates:
        with db.submission_locks.claim(draft_id) as claimed:
            if not claimed:
                continue
            with db.sessions.begin() as session:
                record = session.get(OrderRecord, order_id)
                if record is None or record.state != OrderState.SUBMITTING:
                    continue
                previous_version = record.version
                updated_at = max(now, record.updated_at)
                result = cast(
                    CursorResult[object],
                    session.execute(
                        update(OrderRecord)
                        .where(
                            OrderRecord.id == order_id,
                            OrderRecord.version == previous_version,
                            OrderRecord.state == OrderState.SUBMITTING,
                        )
                        .values(
                            state=OrderState.UNKNOWN,
                            result_code="unknown",
                            result_message=(
                                "Order outcome is unknown. Reconciliation is required."
                            ),
                            result_source=OrderStatusSource.SYSTEM.value,
                            updated_at=updated_at,
                            version=previous_version + 1,
                        )
                    ),
                )
                if result.rowcount != 1:
                    continue
                _invalidate_cancellation_requests_for_order_in_session(
                    session, record.id, "order_state_changed", updated_at
                )
                _append_order_event(
                    session,
                    draft_id=record.draft_id,
                    order_id=record.id,
                    account_id=record.account_id,
                    event_type=OrderEventType.STATUS_TRANSITION,
                    actor=OrderEventActor.SYSTEM,
                    occurred_at=updated_at,
                    previous_state=OrderState.SUBMITTING,
                    next_state=OrderState.UNKNOWN,
                    code=OrderEventCode.UNKNOWN,
                    details={"status_source": OrderStatusSource.SYSTEM},
                    deduplication_key=(
                        f"order:{record.id}:version:{previous_version + 1}:recovery"
                    ),
                )
                recovered += 1
    return recovered


def finish_order_submission(
    db: Database,
    order_id: str,
    state: OrderState,
    now: datetime,
    *,
    expected_version: int,
    broker_order_id: str | None = None,
    result_code: str | None = None,
    result_message: str | None = None,
    fill: FillSummary | None = None,
    result_source: OrderStatusSource = OrderStatusSource.SYSTEM,
    actor: OrderEventActor = OrderEventActor.SYSTEM,
) -> "StoredOrder":
    now = require_aware_utc(now)
    with db.sessions.begin() as session:
        record = session.get(OrderRecord, order_id)
        if record is None:
            raise ValueError("Order not found")
        if record.version != expected_version:
            raise ConcurrentOrderUpdate("Order changed during provider request")
        previous_state = OrderState(record.state)
        require_transition(OrderState(record.state), state)
        if fill is not None:
            _validate_fill(fill, record.quantity, record.filled_quantity)
            if state == OrderState.FILLED and fill.quantity != record.quantity:
                raise ValueError("Filled quantity must equal the order quantity")
            if (
                state == OrderState.PARTIALLY_FILLED
                and fill.quantity >= record.quantity
            ):
                raise ValueError("Partial fill must be below the order quantity")
        record.state = state
        if broker_order_id is not None:
            record.broker_order_id = broker_order_id
        record.result_code = result_code
        record.result_message = result_message
        record.result_source = result_source.value
        if fill is not None:
            record.filled_quantity = fill.quantity
            record.average_fill_price = fill.average_price
        event_occurred_at = max(now, record.updated_at)
        record.updated_at = event_occurred_at
        record.version += 1
        _invalidate_cancellation_requests_for_order_in_session(
            session, record.id, "order_state_changed", event_occurred_at
        )
        session.flush()
        event_type = (
            OrderEventType.SUBMISSION_RESULT
            if previous_state == OrderState.SUBMITTING
            else OrderEventType.STATUS_TRANSITION
        )
        details: dict[str, object] = {"status_source": result_source}
        if fill is not None:
            details["filled_quantity"] = str(fill.quantity)
            if fill.average_price is not None:
                details["average_fill_price"] = str(fill.average_price)
        code = _order_event_code(result_code, state)
        _append_order_event(
            session,
            draft_id=record.draft_id,
            order_id=record.id,
            account_id=record.account_id,
            event_type=event_type,
            actor=actor,
            occurred_at=event_occurred_at,
            previous_state=previous_state,
            next_state=state,
            code=code,
            details=details,
            deduplication_key=(
                f"order:{record.id}:version:{record.version}:{event_type.value}"
            ),
        )
        if result_code == OrderEventCode.DRAFT_EXPIRED.value:
            draft = session.get(OrderDraftRecord, record.draft_id)
            assert draft is not None
            expiry_key = f"draft:{draft.id}:expired"
            if (
                session.scalar(
                    select(OrderEventRecord.event_id).where(
                        OrderEventRecord.deduplication_key == expiry_key
                    )
                )
                is None
            ):
                _append_order_event(
                    session,
                    draft_id=draft.id,
                    order_id=record.id,
                    account_id=record.account_id,
                    event_type=OrderEventType.DRAFT_EXPIRED,
                    actor=actor,
                    occurred_at=event_occurred_at,
                    code=OrderEventCode.DRAFT_EXPIRED,
                    details={"expires_at": draft.expires_at.isoformat()},
                    deduplication_key=expiry_key,
                )
        return _stored_order(record, session.get(OrderDraftRecord, record.draft_id))
