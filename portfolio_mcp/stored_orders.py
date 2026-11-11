import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from portfolio_mcp.execution import FillSummary, OrderState
from portfolio_mcp.order_history import (
    OrderEventCode,
    OrderStatusSource,
)
from portfolio_mcp.schema import (
    OrderAuthorizationRecord,
    OrderDraftRecord,
    OrderRecord,
)


class ConcurrentOrderUpdate(RuntimeError):
    pass


def _validate_fill(
    fill: FillSummary, order_quantity: Decimal, previous_quantity: Decimal | None
) -> None:
    if (
        not fill.quantity.is_finite()
        or fill.quantity <= 0
        or fill.quantity > order_quantity
        or (previous_quantity is not None and fill.quantity < previous_quantity)
        or (
            fill.average_price is not None
            and (not fill.average_price.is_finite() or fill.average_price <= 0)
        )
    ):
        raise ValueError("Invalid order fill")
    if len(str(fill.quantity)) > 128 or (
        fill.average_price is not None and len(str(fill.average_price)) > 128
    ):
        raise ValueError("Order fill exceeds the supported precision")


def _order_event_code(result_code: str | None, state: OrderState) -> OrderEventCode:
    if result_code is not None:
        try:
            return OrderEventCode(result_code)
        except ValueError:
            return OrderEventCode.VALIDATION_FAILED
    return {
        OrderState.ACCEPTED: OrderEventCode.ACCEPTED,
        OrderState.PARTIALLY_FILLED: OrderEventCode.PARTIALLY_FILLED,
        OrderState.FILLED: OrderEventCode.FILLED,
        OrderState.REJECTED: OrderEventCode.REJECTED,
        OrderState.EXPIRED: OrderEventCode.EXPIRED,
        OrderState.UNKNOWN: OrderEventCode.UNKNOWN,
    }.get(state, OrderEventCode.VALIDATION_FAILED)


@dataclass(frozen=True)
class OrderDraft:
    id: str
    account_id: str
    account_label: str
    provider: str
    instrument_id: str
    symbol: str
    instrument_name: str
    asset_class: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    quote_observed_at: datetime | None
    quote_last_price: Decimal | None
    quote_bid_price: Decimal | None
    quote_ask_price: Decimal | None
    quote_source: str | None
    estimated_notional: Decimal | None
    account_refreshed_at: datetime | None
    capability_observed_at: datetime | None
    capability_last_success_at: datetime | None
    warnings: tuple[str, ...]
    fingerprint: str
    created_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "account": {
                "id": self.account_id,
                "label": self.account_label,
                "provider": self.provider,
            },
            "instrument": {
                "id": self.instrument_id,
                "symbol": self.symbol,
                "name": self.instrument_name,
                "asset_class": self.asset_class,
            },
            "instruction": {
                "side": self.side,
                "type": self.order_type,
                "quantity": str(self.quantity),
                "limit_price": (
                    str(self.limit_price) if self.limit_price is not None else None
                ),
                "time_in_force": "day",
            },
            "quote": {
                "observed_at": (
                    self.quote_observed_at.isoformat()
                    if self.quote_observed_at is not None
                    else None
                ),
                "last_price": (
                    str(self.quote_last_price)
                    if self.quote_last_price is not None
                    else None
                ),
                "bid_price": (
                    str(self.quote_bid_price)
                    if self.quote_bid_price is not None
                    else None
                ),
                "ask_price": (
                    str(self.quote_ask_price)
                    if self.quote_ask_price is not None
                    else None
                ),
                "source": self.quote_source,
            },
            "safety": {
                "estimated_notional": (
                    str(self.estimated_notional)
                    if self.estimated_notional is not None
                    else None
                ),
                "account_refreshed_at": (
                    self.account_refreshed_at.isoformat()
                    if self.account_refreshed_at is not None
                    else None
                ),
                "capability_observed_at": (
                    self.capability_observed_at.isoformat()
                    if self.capability_observed_at is not None
                    else None
                ),
                "capability_last_success_at": (
                    self.capability_last_success_at.isoformat()
                    if self.capability_last_success_at is not None
                    else None
                ),
            },
            "warnings": list(self.warnings),
            "fingerprint": self.fingerprint,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class StoredDraftSummary:
    id: str
    created_at: datetime
    expires_at: datetime
    instrument_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "instruction": {
                "instrument_id": self.instrument_id,
                "symbol": self.symbol,
                "side": self.side,
                "type": self.order_type,
                "quantity": str(self.quantity),
                "limit_price": (
                    str(self.limit_price) if self.limit_price is not None else None
                ),
            },
            "warnings": list(self.warnings),
        }


_CANCEL_BLOCKING_REASONS: dict[OrderState, str] = {
    OrderState.FILLED: "Order is already filled",
    OrderState.REJECTED: "Order is rejected",
    OrderState.CANCELED: "Order is already canceled",
    OrderState.EXPIRED: "Order has expired",
    OrderState.UNKNOWN: "Order outcome is unknown; reconciliation is required",
    OrderState.SUBMITTING: "Order submission is in progress",
    OrderState.CANCEL_PENDING: "Cancellation is already in progress",
}


@dataclass(frozen=True)
class StoredOrder:
    id: str
    draft_id: str
    client_order_id: str
    fingerprint: str
    account_id: str
    account_label: str
    provider: str
    instrument_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    state: OrderState
    broker_order_id: str | None
    result_code: str | None
    result_message: str | None
    result_source: OrderStatusSource | None
    filled_quantity: Decimal | None
    average_fill_price: Decimal | None
    provider_submission_started_at: datetime | None
    provider_updated_at: datetime | None
    provider_status_label: str | None
    draft: StoredDraftSummary
    created_at: datetime
    updated_at: datetime
    version: int

    @property
    def remaining_quantity(self) -> Decimal | None:
        if self.filled_quantity is None:
            return None
        return max(Decimal(0), self.quantity - self.filled_quantity)

    @property
    def can_cancel(self) -> bool:
        return self._can_cancel_decision()[0]

    @property
    def blocking_reason(self) -> str | None:
        return self._can_cancel_decision()[1]

    def _can_cancel_decision(self) -> tuple[bool, str | None]:
        blocking = _CANCEL_BLOCKING_REASONS.get(self.state)
        if blocking is not None:
            return False, blocking
        if self.state == OrderState.PARTIALLY_FILLED and (
            self.remaining_quantity is not None
            and self.remaining_quantity <= Decimal(0)
        ):
            return False, "Order has no remaining quantity to cancel"
        if not self.broker_order_id or not self.broker_order_id.strip():
            return False, "Missing broker order ID"
        return True, None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "draft_id": self.draft_id,
            "fingerprint": self.fingerprint,
            "account": {"id": self.account_id, "label": self.account_label},
            "provider": self.provider,
            "instrument": {"id": self.instrument_id, "symbol": self.symbol},
            "instruction": {
                "side": self.side,
                "type": self.order_type,
                "quantity": str(self.quantity),
                "limit_price": (
                    str(self.limit_price) if self.limit_price is not None else None
                ),
                "time_in_force": "day",
            },
            "state": self.state,
            "broker_order_id": self.broker_order_id,
            "result": {
                "code": self.result_code,
                "message": self.result_message,
                "source": (
                    self.result_source.value if self.result_source is not None else None
                ),
            },
            "fill": (
                {
                    "quantity": str(self.filled_quantity),
                    "average_price": (
                        str(self.average_fill_price)
                        if self.average_fill_price is not None
                        else None
                    ),
                }
                if self.filled_quantity is not None
                else None
            ),
            "remaining_quantity": (
                str(self.remaining_quantity)
                if self.remaining_quantity is not None
                else None
            ),
            "provider_submission_started_at": (
                self.provider_submission_started_at.isoformat()
                if self.provider_submission_started_at is not None
                else None
            ),
            "provider_updated_at": (
                self.provider_updated_at.isoformat()
                if self.provider_updated_at is not None
                else None
            ),
            "provider_status_label": self.provider_status_label,
            "draft": self.draft.to_dict(),
            "warnings": list(self.draft.warnings),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "version": self.version,
            "can_cancel": self.can_cancel,
            "blocking_reason": self.blocking_reason,
        }


@dataclass(frozen=True)
class StoredOrderAuthorization:
    id: str
    draft_id: str
    action: str
    expected_fingerprint: str
    account_id: str
    actor: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime


def _order_draft(record: OrderDraftRecord) -> "OrderDraft":
    return OrderDraft(
        id=record.id,
        account_id=record.account_id,
        account_label=record.account_label,
        provider=record.provider,
        instrument_id=record.instrument_id,
        symbol=record.symbol,
        instrument_name=record.instrument_name,
        asset_class=record.asset_class,
        side=record.side,
        order_type=record.order_type,
        quantity=record.quantity,
        limit_price=record.limit_price,
        quote_observed_at=record.quote_observed_at,
        quote_last_price=record.quote_last_price,
        quote_bid_price=record.quote_bid_price,
        quote_ask_price=record.quote_ask_price,
        quote_source=record.quote_source,
        estimated_notional=record.estimated_notional,
        account_refreshed_at=record.account_refreshed_at,
        capability_observed_at=record.capability_observed_at,
        capability_last_success_at=record.capability_last_success_at,
        warnings=tuple(json.loads(record.warnings)),
        fingerprint=record.fingerprint,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


def _stored_order(
    record: OrderRecord, draft: OrderDraftRecord | None = None
) -> "StoredOrder":
    if draft is None:
        raise ValueError("Order draft not found")
    return StoredOrder(
        id=record.id,
        draft_id=record.draft_id,
        client_order_id=record.client_order_id,
        fingerprint=record.fingerprint,
        account_id=record.account_id,
        account_label=record.account_label,
        provider=record.provider,
        instrument_id=record.instrument_id,
        symbol=record.symbol,
        side=record.side,
        order_type=record.order_type,
        quantity=record.quantity,
        limit_price=record.limit_price,
        state=OrderState(record.state),
        broker_order_id=record.broker_order_id,
        result_code=record.result_code,
        result_message=record.result_message,
        result_source=(
            OrderStatusSource(record.result_source)
            if record.result_source is not None
            else None
        ),
        filled_quantity=record.filled_quantity,
        average_fill_price=record.average_fill_price,
        provider_submission_started_at=record.provider_submission_started_at,
        provider_updated_at=record.provider_updated_at,
        provider_status_label=record.provider_status_label,
        draft=StoredDraftSummary(
            id=draft.id,
            created_at=draft.created_at,
            expires_at=draft.expires_at,
            instrument_id=draft.instrument_id,
            symbol=draft.symbol,
            side=draft.side,
            order_type=draft.order_type,
            quantity=draft.quantity,
            limit_price=draft.limit_price,
            warnings=tuple(json.loads(draft.warnings) if draft.warnings else ()),
        ),
        created_at=record.created_at,
        updated_at=record.updated_at,
        version=record.version,
    )


def _stored_authorization(
    record: OrderAuthorizationRecord,
) -> "StoredOrderAuthorization":
    return StoredOrderAuthorization(
        id=record.id,
        draft_id=record.draft_id,
        action=record.action,
        expected_fingerprint=record.expected_fingerprint,
        account_id=record.account_id,
        actor=record.actor,
        created_at=record.created_at,
        expires_at=record.expires_at,
        consumed_at=record.consumed_at,
    )
