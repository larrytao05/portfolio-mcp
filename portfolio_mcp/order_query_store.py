from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    func,
    select,
)

from portfolio_mcp.database import Database
from portfolio_mcp.execution import OrderState
from portfolio_mcp.order_history import (
    OrderAuditFilters,
    OrderCursor,
    OrderEventActor,
    OrderEventCode,
    OrderEventCursor,
    OrderEventPage,
    OrderEventType,
    OrderListFilters,
    OrderPage,
    StoredOrderEvent,
    decode_event_details,
    encode_event_details,
    order_audit_filter_digest,
    order_list_filter_digest,
    require_aware_utc,
)
from portfolio_mcp.schema import (
    OrderDraftRecord,
    OrderEventRecord,
    OrderRecord,
)
from portfolio_mcp.stored_orders import (
    OrderDraft,
    StoredOrder,
    _order_draft,
    _stored_order,
)


def _validate_page_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Page limit must be between 1 and 100")


def _epoch_microseconds(value: datetime) -> int:
    normalized = require_aware_utc(value)
    delta = normalized - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


class CorruptedAuditRecordError(ValueError):
    """Raised when an audit record in the database fails decoding."""


def _stored_order_event(record: OrderEventRecord) -> StoredOrderEvent:
    try:
        event_type = OrderEventType(record.event_type)
        details = decode_event_details(record.details_json)
        encode_event_details(event_type, details)
        actor = OrderEventActor(record.actor)
        code = OrderEventCode(record.code) if record.code is not None else None
    except (ValueError, TypeError, KeyError) as error:
        raise CorruptedAuditRecordError(
            f"Corrupted audit record {record.event_id}"
        ) from error
    return StoredOrderEvent(
        event_id=UUID(record.event_id),
        draft_id=record.draft_id,
        order_id=record.order_id,
        account_id=record.account_id,
        event_type=event_type,
        actor=actor,
        previous_state=record.previous_state,
        next_state=record.next_state,
        code=code,
        details=details,
        occurred_at=record.occurred_at,
    )


def order_draft(db: Database, draft_id: str) -> "OrderDraft | None":
    with db.sessions() as session:
        record = session.get(OrderDraftRecord, draft_id)
        return _order_draft(record) if record is not None else None


def order(db: Database, order_id: str) -> "StoredOrder | None":
    with db.sessions() as session:
        record = session.get(OrderRecord, order_id)
        if record is None:
            return None
        return _stored_order(record, session.get(OrderDraftRecord, record.draft_id))


def order_for_draft(db: Database, draft_id: str) -> "StoredOrder | None":
    with db.sessions() as session:
        record = session.scalar(
            select(OrderRecord).where(OrderRecord.draft_id == draft_id)
        )
        if record is None:
            return None
        return _stored_order(record, session.get(OrderDraftRecord, record.draft_id))


def list_orders(
    db: Database,
    *,
    limit: int = 50,
    after: OrderCursor | None = None,
    account_id: str | None = None,
    states: tuple[OrderState, ...] = (),
    provider: str | None = None,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> OrderPage["StoredOrder"]:
    _validate_page_limit(limit)
    normalized_states = tuple(sorted({state.value for state in states}))
    filters = OrderListFilters(
        account_id=account_id,
        provider=provider,
        symbol=symbol,
        states=normalized_states,
        start_date=start_date,
        end_date=end_date,
    )
    filter_digest = order_list_filter_digest(filters, limit)
    if after is not None:
        require_aware_utc(after.created_at)
    if after is not None and after.filter_digest != filter_digest:
        raise ValueError("Order cursor does not match the requested filters")
    with db.sessions() as session:
        statement = select(OrderRecord, OrderDraftRecord).join(
            OrderDraftRecord, OrderDraftRecord.id == OrderRecord.draft_id
        )
        if account_id is not None:
            statement = statement.where(OrderRecord.account_id == account_id)
        if states:
            statement = statement.where(OrderRecord.state.in_(normalized_states))
        if provider is not None:
            statement = statement.where(
                func.lower(OrderRecord.provider) == provider.casefold()
            )
        if symbol is not None:
            statement = statement.where(
                func.upper(OrderRecord.symbol) == symbol.upper()
            )
        if start_date is not None:
            statement = statement.where(
                OrderRecord.created_at
                >= datetime.combine(start_date, datetime.min.time(), UTC)
            )
        if end_date is not None:
            statement = statement.where(
                OrderRecord.created_at
                < datetime.combine(
                    end_date + timedelta(days=1), datetime.min.time(), UTC
                )
            )
        if after is not None:
            statement = statement.where(
                (OrderRecord.created_at < after.created_at)
                | (
                    (OrderRecord.created_at == after.created_at)
                    & (OrderRecord.id < after.order_id)
                )
            )
        rows = list(
            session.execute(
                statement.order_by(
                    OrderRecord.created_at.desc(), OrderRecord.id.desc()
                ).limit(limit + 1)
            )
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(_stored_order(order, draft) for order, draft in rows)
        cursor = None
        if has_more and rows:
            last = rows[-1][0]
            cursor = OrderCursor(
                created_at=last.created_at,
                order_id=last.id,
                filter_digest=filter_digest,
            )
        return OrderPage(items, cursor)


def list_order_events(
    db: Database,
    *,
    limit: int = 50,
    after: OrderEventCursor | None = None,
    draft_id: str | None = None,
    order_id: str | None = None,
    account_id: str | None = None,
    provider: str | None = None,
    symbol: str | None = None,
    states: tuple[str, ...] = (),
    start_date: date | None = None,
    end_date: date | None = None,
) -> OrderEventPage:
    _validate_page_limit(limit)
    normalized_states = tuple(sorted(set(states)))
    filters = OrderAuditFilters(
        order_id=order_id,
        draft_id=draft_id,
        account_id=account_id,
        provider=provider,
        symbol=symbol,
        states=normalized_states,
        start_date=start_date,
        end_date=end_date,
    )
    filter_digest = order_audit_filter_digest(filters, limit)
    if after is not None:
        require_aware_utc(after.occurred_at)
    if order_id is not None and draft_id is not None:
        raise ValueError("Specify order_id or draft_id, not both")
    if after is not None and after.filter_digest != filter_digest:
        raise ValueError("Order event cursor does not match the requested filters")
    with db.sessions() as session:
        statement = select(OrderEventRecord)
        if order_id is not None:
            order = session.get(OrderRecord, order_id)
            if order is None:
                return OrderEventPage((), None)
            statement = statement.where(
                (OrderEventRecord.order_id == order_id)
                | (OrderEventRecord.draft_id == order.draft_id)
            )
        elif draft_id is not None:
            statement = statement.where(OrderEventRecord.draft_id == draft_id)
        if account_id is not None:
            statement = statement.where(OrderEventRecord.account_id == account_id)
        if provider is not None or symbol is not None:
            statement = statement.outerjoin(
                OrderRecord, OrderRecord.id == OrderEventRecord.order_id
            ).outerjoin(
                OrderDraftRecord, OrderDraftRecord.id == OrderEventRecord.draft_id
            )
            if provider is not None:
                statement = statement.where(
                    func.lower(
                        func.coalesce(OrderRecord.provider, OrderDraftRecord.provider)
                    )
                    == provider.casefold()
                )
            if symbol is not None:
                statement = statement.where(
                    func.upper(
                        func.coalesce(OrderRecord.symbol, OrderDraftRecord.symbol)
                    )
                    == symbol.upper()
                )
        if normalized_states:
            statement = statement.where(
                OrderEventRecord.next_state.in_(normalized_states)
            )
        if start_date is not None:
            statement = statement.where(
                OrderEventRecord.occurred_at
                >= datetime.combine(start_date, datetime.min.time(), UTC)
            )
        if end_date is not None:
            statement = statement.where(
                OrderEventRecord.occurred_at
                < datetime.combine(
                    end_date + timedelta(days=1), datetime.min.time(), UTC
                )
            )
        if after is not None:
            statement = statement.where(
                (OrderEventRecord.occurred_at < after.occurred_at)
                | (
                    (OrderEventRecord.occurred_at == after.occurred_at)
                    & (OrderEventRecord.event_id < str(after.event_id))
                )
            )
        records = list(
            session.scalars(
                statement.order_by(
                    OrderEventRecord.occurred_at.desc(),
                    OrderEventRecord.event_id.desc(),
                ).limit(limit + 1)
            )
        )
        has_more = len(records) > limit
        records = records[:limit]
        items = tuple(_stored_order_event(record) for record in records)
        cursor = None
        if has_more and records:
            last = records[-1]
            cursor = OrderEventCursor(
                occurred_at=last.occurred_at,
                event_id=UUID(last.event_id),
                draft_id=draft_id,
                order_id=order_id,
                account_id=account_id,
                filter_digest=filter_digest,
            )
        return OrderEventPage(items, cursor)


def order_exists(db: Database, order_id: str) -> bool:
    with db.sessions() as session:
        return session.get(OrderRecord, order_id) is not None


def order_draft_exists(db: Database, draft_id: str) -> bool:
    with db.sessions() as session:
        return session.get(OrderDraftRecord, draft_id) is not None
