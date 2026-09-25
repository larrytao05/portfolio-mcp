from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Generic, TypeAlias, TypeVar
from uuid import UUID


class OrderEventActor(StrEnum):
    DASHBOARD = "dashboard"
    MCP = "mcp"
    SYSTEM = "system"


class OrderEventType(StrEnum):
    DRAFT_CREATED = "draft_created"
    DRAFT_EXPIRED = "draft_expired"
    AUTHORIZATION_CREATED = "authorization_created"
    AUTHORIZATION_CONSUMED = "authorization_consumed"
    AUTHORIZATION_FAILED = "authorization_failed"
    SUBMISSION_STARTED = "submission_started"
    SUBMISSION_RESULT = "submission_result"
    STATUS_TRANSITION = "status_transition"
    RECONCILIATION_ATTEMPTED = "reconciliation_attempted"
    RECONCILIATION_RESULT = "reconciliation_result"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLATION_RESULT = "cancellation_result"


class OrderEventCode(StrEnum):
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    MATCHED = "matched"
    DRAFT_EXPIRED = "draft_expired"
    VALIDATION_FAILED = "validation_failed"
    AUTHORIZATION_INVALID = "authorization_invalid"
    CONFIRMATION_REQUIRED = "confirmation_required"
    DRAFT_NOT_FOUND = "draft_not_found"
    DRAFT_CHANGED = "draft_changed"
    QUOTE_UNAVAILABLE = "quote_unavailable"
    QUOTE_STALE = "quote_stale"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    EXECUTION_UNSUPPORTED = "execution_unsupported"
    TRADING_BLOCKED = "trading_blocked"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    INCOMPLETE = "incomplete"
    MISMATCH = "mismatch"
    STALE = "stale"
    PROVIDER_ERROR = "provider_error"
    CANCELED = "canceled"


class OrderStatusSource(StrEnum):
    PROVIDER = "provider"
    LOCAL = "local"
    SYSTEM = "system"


EventValue: TypeAlias = str | int | bool | None
EventDetails: TypeAlias = Mapping[str, EventValue]


@dataclass(frozen=True)
class OrderEventCursor:
    occurred_at: datetime
    event_id: UUID
    draft_id: str | None
    order_id: str | None
    account_id: str | None


@dataclass(frozen=True)
class OrderCursor:
    updated_at: datetime
    order_id: str
    account_id: str | None
    states: tuple[str, ...]


T = TypeVar("T")


@dataclass(frozen=True)
class OrderPage(Generic[T]):
    items: tuple[T, ...]
    next_cursor: OrderCursor | None


@dataclass(frozen=True)
class StoredOrderEvent:
    event_id: UUID
    draft_id: str | None
    order_id: str | None
    account_id: str | None
    event_type: OrderEventType
    actor: OrderEventActor
    previous_state: str | None
    next_state: str | None
    code: OrderEventCode | None
    details: EventDetails
    occurred_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "draft_id": self.draft_id,
            "order_id": self.order_id,
            "type": self.event_type.value,
            "actor": self.actor.value,
            "previous_state": self.previous_state,
            "next_state": self.next_state,
            "code": self.code.value if self.code is not None else None,
            "details": dict(self.details),
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass(frozen=True)
class OrderEventPage:
    items: tuple[StoredOrderEvent, ...]
    next_cursor: OrderEventCursor | None


@dataclass(frozen=True)
class EventDetailsSchema:
    allowed: frozenset[str]
    required: frozenset[str] = frozenset()


_EVENT_DETAILS: dict[OrderEventType, EventDetailsSchema] = {
    OrderEventType.DRAFT_CREATED: EventDetailsSchema(frozenset()),
    OrderEventType.DRAFT_EXPIRED: EventDetailsSchema(
        frozenset({"expires_at"}), frozenset({"expires_at"})
    ),
    OrderEventType.AUTHORIZATION_CREATED: EventDetailsSchema(
        frozenset({"authorization_id", "action"}),
        frozenset({"authorization_id", "action"}),
    ),
    OrderEventType.AUTHORIZATION_CONSUMED: EventDetailsSchema(
        frozenset({"authorization_id", "action"}),
        frozenset({"authorization_id", "action"}),
    ),
    OrderEventType.AUTHORIZATION_FAILED: EventDetailsSchema(
        frozenset({"attempt_id", "action"}), frozenset({"attempt_id", "action"})
    ),
    OrderEventType.SUBMISSION_STARTED: EventDetailsSchema(frozenset()),
    OrderEventType.SUBMISSION_RESULT: EventDetailsSchema(
        frozenset({"status_source", "filled_quantity", "average_fill_price"}),
        frozenset({"status_source"}),
    ),
    OrderEventType.STATUS_TRANSITION: EventDetailsSchema(
        frozenset({"status_source", "filled_quantity", "average_fill_price"}),
        frozenset({"status_source"}),
    ),
    OrderEventType.RECONCILIATION_ATTEMPTED: EventDetailsSchema(
        frozenset({"attempt_id"}), frozenset({"attempt_id"})
    ),
    OrderEventType.RECONCILIATION_RESULT: EventDetailsSchema(
        frozenset(
            {
                "attempt_id",
                "outcome",
                "status_source",
                "filled_quantity",
                "average_fill_price",
                "provider_updated_at",
                "provider_status_label",
            }
        ),
        frozenset({"attempt_id", "outcome"}),
    ),
    OrderEventType.CANCELLATION_REQUESTED: EventDetailsSchema(frozenset()),
    OrderEventType.CANCELLATION_RESULT: EventDetailsSchema(
        frozenset({"outcome"}), frozenset({"outcome"})
    ),
}
if set(_EVENT_DETAILS) != set(OrderEventType):
    raise RuntimeError("Every order event type must define a details schema")


def encode_event_details(
    event_type: OrderEventType, details: Mapping[str, object]
) -> str:
    schema = _EVENT_DETAILS.get(event_type)
    if schema is None:
        raise ValueError(f"Invalid order event type: {event_type}")
    if not schema.allowed.issuperset(details.keys()):
        raise ValueError(f"Invalid details for order event: {event_type.value}")
    if not schema.required.issubset(details):
        raise ValueError(f"Invalid details for order event: {event_type.value}")
    normalized: dict[str, str] = {}
    for key, value in details.items():
        if key in {"authorization_id", "attempt_id"}:
            try:
                normalized[key] = str(UUID(str(value)))
            except (TypeError, ValueError, AttributeError) as error:
                raise ValueError(f"Invalid {key} for order event") from error
            continue
        if key == "action" and value not in {"submit", "cancel"}:
            raise ValueError("Invalid authorization action for order event")
        if key == "status_source" and value not in set(OrderStatusSource):
            raise ValueError("Invalid status source for order event")
        if key == "outcome" and value not in {
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
            raise ValueError("Invalid outcome for order event")
        if key in {"filled_quantity", "average_fill_price"}:
            if not isinstance(value, str) or len(value) > 128:
                raise ValueError(f"Invalid {key} for order event")
            normalized[key] = value
            continue
        if key == "expires_at":
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError("Invalid expiry for order event")
            normalized[key] = value
            continue
        if key == "provider_status_label":
            if value not in {
                "OPEN",
                "PARTIALLY_FILLED",
                "FILLED",
                "REJECTED",
                "CANCELED",
                "EXPIRED",
            }:
                raise ValueError("Invalid provider status label for order event")
            normalized[key] = str(value)
            continue
        if key == "provider_updated_at":
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError("Invalid provider timestamp for order event")
            try:
                require_aware_utc(datetime.fromisoformat(value))
            except ValueError as error:
                raise ValueError(
                    "Invalid provider timestamp for order event"
                ) from error
            normalized[key] = value
            continue
        if isinstance(value, StrEnum):
            normalized[key] = value.value
        elif isinstance(value, str) and len(value) <= 64:
            normalized[key] = value
        elif isinstance(value, bool):
            normalized[key] = "true" if value else "false"
        elif isinstance(value, int) and 0 <= value <= 2**63 - 1:
            normalized[key] = str(value)
        else:
            raise ValueError(f"Invalid {key} for order event")
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 2048:
        raise ValueError("Order event details exceed the size limit")
    return encoded


def decode_event_details(raw: str) -> dict[str, str]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in parsed.items()
    ):
        raise ValueError("Malformed order event details")
    return parsed


def require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Order event timestamps must be timezone-aware")
    return value.astimezone(UTC)
