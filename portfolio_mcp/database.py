import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from alembic.config import Config
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult, Engine, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from alembic import command
from portfolio_mcp.execution import FillSummary, OrderState, require_transition
from portfolio_mcp.models import (
    Account,
    AccountCapabilities,
    CapabilityBlock,
    HoldingsSnapshot,
    Position,
    ProviderHealth,
    ProviderHealthState,
    Transaction,
)
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
    OrderStatusSource,
    StoredOrderEvent,
    decode_event_details,
    encode_event_details,
    order_audit_filter_digest,
    order_list_filter_digest,
    require_aware_utc,
)


class Base(DeclarativeBase):
    pass


class UtcTimestamp(TypeDecorator[datetime]):
    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat()

    def process_result_value(
        self, value: str | None, dialect: object
    ) -> datetime | None:
        return datetime.fromisoformat(value) if value is not None else None


class ExactDecimal(TypeDecorator[Decimal]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: object) -> str | None:
        return str(value) if value is not None else None

    def process_result_value(
        self, value: str | None, dialect: object
    ) -> Decimal | None:
        return Decimal(value) if value is not None else None


class AccountRecord(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    account_type: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PositionRecord(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    market_value: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    cost_basis: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class DailyAccountValueRecord(Base):
    __tablename__ = "daily_account_values"
    __table_args__ = (UniqueConstraint("account_id", "snapshot_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)


class RefreshRunRecord(Base):
    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    account_count: Mapped[int] = mapped_column(Integer, nullable=False)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(256))


class RefreshProviderOutcomeRecord(Base):
    __tablename__ = "refresh_provider_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    refresh_id: Mapped[int] = mapped_column(
        ForeignKey("refresh_runs.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    accounts_refreshed: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    warning: Mapped[str | None] = mapped_column(String(256))


class ActivityRecord(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("account_id", "provider_transaction_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    provider_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    transaction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    amount: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    fees: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)


class AccountCapabilityRecord(Base):
    __tablename__ = "account_capabilities"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_classes: Mapped[str] = mapped_column(Text, nullable=False)
    supported_sides: Mapped[str] = mapped_column(Text, nullable=False)
    order_types: Mapped[str] = mapped_column(Text, nullable=False)
    time_in_force: Mapped[str] = mapped_column(Text, nullable=False)
    sizing_modes: Mapped[str] = mapped_column(Text, nullable=False)
    preview_supported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cancellation_supported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    last_success_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    blocks: Mapped[str] = mapped_column(Text, nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TradingSettingsRecord(Base):
    __tablename__ = "trading_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    max_order_shares: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    max_order_notional_usd: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    updated_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class OrderDraftRecord(Base):
    __tablename__ = "order_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    account_label: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument_name: Mapped[str] = mapped_column(String(256), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    quote_observed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    quote_last_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    quote_bid_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    quote_ask_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    quote_source: Mapped[str | None] = mapped_column(String(64))
    estimated_notional: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    account_refreshed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    capability_observed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    capability_last_success_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    warnings: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)


class OrderRecord(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("draft_id"),
        UniqueConstraint("client_order_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("order_drafts.id"), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    account_label: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(128))
    result_code: Mapped[str | None] = mapped_column(String(64))
    result_message: Mapped[str | None] = mapped_column(String(256))
    result_source: Mapped[str | None] = mapped_column(String(16))
    filled_quantity: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    average_fill_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    provider_submission_started_at: Mapped[datetime | None] = mapped_column(
        UtcTimestamp()
    )
    provider_updated_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    provider_status_label: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class OrderAuthorizationRecord(Base):
    __tablename__ = "order_authorizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("order_drafts.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)


class OrderEventRecord(Base):
    __tablename__ = "order_events"
    __table_args__ = (
        UniqueConstraint("deduplication_key"),
        CheckConstraint(
            "actor IN ('dashboard', 'mcp', 'system')", name="ck_order_events_actor"
        ),
        CheckConstraint(
            "event_type IN ("
            "'draft_created', 'draft_expired', 'authorization_created', "
            "'authorization_consumed', 'authorization_failed', "
            "'submission_started', 'submission_result', 'status_transition', "
            "'reconciliation_attempted', 'reconciliation_result', "
            "'cancellation_requested', 'cancellation_result')",
            name="ck_order_events_type",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("order_drafts.id", ondelete="RESTRICT")
    )
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT")
    )
    account_id: Mapped[str | None] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(32))
    next_state: Mapped[str | None] = mapped_column(String(32))
    code: Mapped[str | None] = mapped_column(String(64))
    details_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)


class ReconciliationGateRecord(Base):
    __tablename__ = "order_reconciliation_gates"
    __table_args__ = (UniqueConstraint("provider", "account_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_attempt_at_us: Mapped[int] = mapped_column(Integer, nullable=False)


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


class ConcurrentOrderUpdate(RuntimeError):
    pass


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


@dataclass(frozen=True)
class StoredPosition:
    position: Position
    as_of: date
    is_stale: bool
    source_refreshed_at: datetime

    def to_dict(self) -> dict[str, str | bool | None]:
        gain_loss = (
            self.position.market_value - self.position.cost_basis
            if self.position.market_value is not None
            and self.position.cost_basis is not None
            else None
        )
        return {
            "account_id": self.position.account_id,
            "as_of": self.as_of.isoformat(),
            "is_stale": self.is_stale,
            "source_refreshed_at": self.source_refreshed_at.isoformat(),
            **self.position.to_dict(),
            "gain_loss": str(gain_loss) if gain_loss is not None else None,
        }


@dataclass(frozen=True)
class StoredAccount:
    account: Account
    is_stale: bool
    source_refreshed_at: datetime

    def to_dict(self) -> dict[str, str | bool]:
        return {
            **self.account.to_dict(),
            "is_stale": self.is_stale,
            "source_refreshed_at": self.source_refreshed_at.isoformat(),
        }


@dataclass(frozen=True)
class StoredTradingSettings:
    live_trading_enabled: bool
    kill_switch_active: bool
    max_order_shares: Decimal | None
    max_order_notional_usd: Decimal | None
    updated_at: datetime | None
    version: int


@dataclass(frozen=True)
class ProviderRefreshOutcome:
    provider: str
    status: str
    accounts_refreshed: int
    stale_accounts: int
    excluded_accounts: int
    warning: str | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "provider": self.provider,
            "status": self.status,
            "accounts_refreshed": self.accounts_refreshed,
            "stale_accounts": self.stale_accounts,
            "excluded_accounts": self.excluded_accounts,
            "warning": self.warning,
        }


@dataclass(frozen=True)
class AccountDetail:
    account: Account
    refreshed_at: datetime
    as_of: date | None
    positions: tuple[StoredPosition, ...]

    def to_dict(self) -> dict[str, object]:
        market_values = [
            stored.position.market_value
            for stored in self.positions
            if stored.position.market_value is not None
        ]
        cost_bases = [
            stored.position.cost_basis
            for stored in self.positions
            if stored.position.cost_basis is not None
        ]
        currencies_match = all(
            stored.position.currency == self.account.currency
            for stored in self.positions
        )
        return {
            "id": self.account.id,
            "provider": self.account.provider,
            "label": self.account.label,
            "account_type": self.account.account_type,
            "currency": self.account.currency,
            "refreshed_at": self.refreshed_at.isoformat(),
            "as_of": self.as_of.isoformat() if self.as_of is not None else None,
            "balances": {
                "market_value": self._total_if_complete(
                    market_values, currencies_match
                ),
                "cost_basis": self._total_if_complete(cost_bases, currencies_match),
                "currency": self.account.currency,
            },
            "positions": [position.to_dict() for position in self.positions],
        }

    def _total_if_complete(
        self, values: list[Decimal], currencies_match: bool
    ) -> str | None:
        if not currencies_match or len(values) != len(self.positions):
            return None
        return str(sum(values, start=Decimal("0")))


@dataclass(frozen=True)
class DailyAccountValue:
    account_id: str
    snapshot_date: date
    value: Decimal
    currency: str
    recorded_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {
            "account_id": self.account_id,
            "snapshot_date": self.snapshot_date.isoformat(),
            "value": str(self.value),
            "currency": self.currency,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass(frozen=True)
class RefreshResult:
    id: int
    status: str
    started_at: datetime
    completed_at: datetime
    account_count: int
    position_count: int
    snapshot_count: int
    error_code: str | None = None
    error_message: str | None = None
    provider_outcomes: tuple[ProviderRefreshOutcome, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "accounts_refreshed": self.account_count,
            "positions_refreshed": self.position_count,
            "daily_snapshots_recorded": self.snapshot_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "provider_outcomes": [
                outcome.to_dict() for outcome in self.provider_outcomes
            ],
            "warnings": [
                outcome.warning
                for outcome in self.provider_outcomes
                if outcome.warning is not None
            ],
        }


def create_engine_for(database_url: str) -> Engine:
    return create_engine(database_url)


def upgrade_database(database_url: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


class PortfolioRepository:
    def __init__(
        self,
        database_url: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        upgrade_database(database_url)
        self._sessions = sessionmaker(
            create_engine_for(database_url), expire_on_commit=False
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def save_order_draft(
        self,
        draft: "OrderDraft",
        actor: OrderEventActor = OrderEventActor.DASHBOARD,
    ) -> None:
        with self._sessions.begin() as session:
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

    def trading_settings(self) -> StoredTradingSettings:
        with self._sessions() as session:
            record = session.get(TradingSettingsRecord, 1)
            if record is None:
                return StoredTradingSettings(False, True, None, None, None, 0)
            return self._stored_trading_settings(record)

    def stored_account(self, account_id: str) -> StoredAccount | None:
        with self._sessions() as session:
            record = session.get(AccountRecord, account_id)
            if record is None:
                return None
            return self._stored_account(record)

    def replace_trading_settings(
        self,
        *,
        live_trading_enabled: bool,
        kill_switch_active: bool,
        max_order_shares: Decimal | None,
        max_order_notional_usd: Decimal | None,
        expected_version: int,
        updated_at: datetime,
    ) -> StoredTradingSettings | None:
        values = {
            "live_trading_enabled": live_trading_enabled,
            "kill_switch_active": kill_switch_active,
            "max_order_shares": max_order_shares,
            "max_order_notional_usd": max_order_notional_usd,
            "updated_at": updated_at,
            "version": expected_version + 1,
        }
        with self._sessions.begin() as session:
            if expected_version == 0:
                inserted = cast(
                    CursorResult[object],
                    session.execute(
                        sqlite_insert(TradingSettingsRecord)
                        .values(id=1, **values)
                        .on_conflict_do_nothing(index_elements=("id",))
                    ),
                )
                if inserted.rowcount == 1:
                    record = session.get(TradingSettingsRecord, 1)
                    assert record is not None
                    return self._stored_trading_settings(record)
            updated = cast(
                CursorResult[object],
                session.execute(
                    update(TradingSettingsRecord)
                    .where(
                        TradingSettingsRecord.id == 1,
                        TradingSettingsRecord.version == expected_version,
                    )
                    .values(**values)
                ),
            )
            if updated.rowcount != 1:
                return None
            record = session.get(TradingSettingsRecord, 1)
            assert record is not None
            return self._stored_trading_settings(record)

    def order_draft(self, draft_id: str) -> "OrderDraft | None":
        with self._sessions() as session:
            record = session.get(OrderDraftRecord, draft_id)
            return self._order_draft(record) if record is not None else None

    def begin_order_submission(
        self,
        draft: "OrderDraft",
        now: datetime,
        actor: OrderEventActor = OrderEventActor.DASHBOARD,
    ) -> tuple["StoredOrder", bool]:
        now = require_aware_utc(now)
        order_id = str(uuid4())
        client_order_id = str(uuid4())
        try:
            with self._sessions.begin() as session:
                existing = session.scalar(
                    select(OrderRecord).where(OrderRecord.draft_id == draft.id)
                )
                if existing is not None:
                    return self._stored_order(
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
                        actor="dashboard-owner",
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
                return self._stored_order(
                    record, session.get(OrderDraftRecord, draft.id)
                ), True
        except IntegrityError:
            with self._sessions() as session:
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
                return self._stored_order(
                    existing, session.get(OrderDraftRecord, existing.draft_id)
                ), False

    def mark_provider_submission_started(
        self, order_id: str, *, expected_version: int, started_at: datetime
    ) -> "StoredOrder":
        started_at = require_aware_utc(started_at)
        with self._sessions.begin() as session:
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
            return self._stored_order(
                record, session.get(OrderDraftRecord, record.draft_id)
            )

    def order(self, order_id: str) -> "StoredOrder | None":
        with self._sessions() as session:
            record = session.get(OrderRecord, order_id)
            if record is None:
                return None
            return self._stored_order(
                record, session.get(OrderDraftRecord, record.draft_id)
            )

    def order_for_draft(self, draft_id: str) -> "StoredOrder | None":
        with self._sessions() as session:
            record = session.scalar(
                select(OrderRecord).where(OrderRecord.draft_id == draft_id)
            )
            if record is None:
                return None
            return self._stored_order(
                record, session.get(OrderDraftRecord, record.draft_id)
            )

    def record_draft_expiry(self, draft_id: str, *, observed_at: datetime) -> bool:
        observed_at = require_aware_utc(observed_at)
        with self._sessions.begin() as session:
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
        self,
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
        with self._sessions.begin() as session:
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

    def list_orders(
        self,
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
        with self._sessions() as session:
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
            items = tuple(self._stored_order(order, draft) for order, draft in rows)
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
        self,
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
        with self._sessions() as session:
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
                            func.coalesce(
                                OrderRecord.provider, OrderDraftRecord.provider
                            )
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

    def account_exists(self, account_id: str) -> bool:
        with self._sessions() as session:
            return (
                session.scalar(
                    select(AccountRecord.id).where(AccountRecord.id == account_id)
                )
                is not None
            )

    def order_exists(self, order_id: str) -> bool:
        with self._sessions() as session:
            return session.get(OrderRecord, order_id) is not None

    def order_draft_exists(self, draft_id: str) -> bool:
        with self._sessions() as session:
            return session.get(OrderDraftRecord, draft_id) is not None

    def order_refresh_plans(
        self,
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
        with self._sessions() as session:
            for provider, account_id in normalized_groups:
                target_id = self._reconciliation_target(session, provider, account_id)
                if target_id is None:
                    continue
                next_at = self._reconciliation_gate_next_at(
                    session, provider, account_id, now, minimum_interval
                )
                plans.append(OrderRefreshPlan(provider, account_id, target_id, next_at))
        return tuple(plans)

    def next_order_reconciliation_at(
        self,
        provider: str,
        account_id: str,
        *,
        now: datetime,
        minimum_interval: timedelta,
    ) -> datetime:
        now = require_aware_utc(now)
        with self._sessions() as session:
            return self._reconciliation_gate_next_at(
                session, provider, account_id, now, minimum_interval
            )

    def claim_planned_order_reconciliation(
        self,
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
        with self._sessions.begin() as session:
            record = session.get(OrderRecord, order_id)
            if record is None:
                raise ValueError("Order not found")
            target_id = self._reconciliation_target(
                session, record.provider, record.account_id
            )
            next_at = self._reconciliation_gate_next_at(
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
            claim = self._claim_order_reconciliation(
                session, record, attempt_at, minimum_interval
            )
            if claim is None:
                next_at = self._reconciliation_gate_next_at(
                    session,
                    record.provider,
                    record.account_id,
                    attempt_at,
                    minimum_interval,
                )
                return OrderReconciliationDecision(
                    None, "throttled", next_at, target_id
                )
            return OrderReconciliationDecision(
                claim, "attempted", attempt_at + minimum_interval, target_id
            )

    def claim_order_reconciliation(
        self, order_id: str, *, attempt_at: datetime, minimum_interval: timedelta
    ) -> "ReconciliationClaim | None":
        attempt_at = require_aware_utc(attempt_at)
        if minimum_interval < timedelta(0):
            raise ValueError("Reconciliation interval must be nonnegative")
        with self._sessions.begin() as session:
            record = session.get(OrderRecord, order_id)
            if record is None:
                raise ValueError("Order not found")
            return self._claim_order_reconciliation(
                session, record, attempt_at, minimum_interval
            )

    def _claim_order_reconciliation(
        self,
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
            order=self._stored_order(
                record, session.get(OrderDraftRecord, record.draft_id)
            ),
            attempt_id=attempt_id,
        )

    def _reconciliation_target(
        self, session: Session, provider: str, account_id: str
    ) -> str | None:
        attempted = (
            select(
                OrderEventRecord.order_id.label("order_id"),
                func.max(OrderEventRecord.occurred_at).label("last_attempt_at"),
            )
            .where(
                OrderEventRecord.event_type
                == OrderEventType.RECONCILIATION_ATTEMPTED.value
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
        self,
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
        self,
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
        with self._sessions.begin() as session:
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
            filled_quantity = (
                fill.quantity if fill is not None else record.filled_quantity
            )
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
                "provider_updated_at": provider_updated_at
                or record.provider_updated_at,
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
            return self._stored_order(
                changed, session.get(OrderDraftRecord, changed.draft_id)
            )

    def authorization_for_draft(
        self, draft_id: str
    ) -> "StoredOrderAuthorization | None":
        with self._sessions() as session:
            record = session.scalar(
                select(OrderAuthorizationRecord)
                .where(OrderAuthorizationRecord.draft_id == draft_id)
                .order_by(OrderAuthorizationRecord.created_at.desc())
            )
            return self._stored_authorization(record) if record is not None else None

    def recover_stranded_submissions(self, now: datetime) -> int:
        now = require_aware_utc(now)
        with self._sessions.begin() as session:
            records = list(
                session.scalars(
                    select(OrderRecord).where(
                        OrderRecord.state == OrderState.SUBMITTING
                    )
                )
            )
            recovered = 0
            for record in records:
                previous_version = record.version
                updated_at = max(now, record.updated_at)
                result = cast(
                    CursorResult[object],
                    session.execute(
                        update(OrderRecord)
                        .where(
                            OrderRecord.id == record.id,
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
                recovered += 1
                _append_order_event(
                    session,
                    draft_id=record.draft_id,
                    order_id=record.id,
                    account_id=record.account_id,
                    event_type=OrderEventType.STATUS_TRANSITION,
                    actor=OrderEventActor.SYSTEM,
                    occurred_at=now,
                    previous_state=OrderState.SUBMITTING,
                    next_state=OrderState.UNKNOWN,
                    code=OrderEventCode.UNKNOWN,
                    details={"status_source": OrderStatusSource.SYSTEM},
                    deduplication_key=(
                        f"order:{record.id}:version:{previous_version + 1}:recovery"
                    ),
                )
            return recovered

    def has_stranded_submissions(self) -> bool:
        with self._sessions() as session:
            return (
                session.scalar(
                    select(OrderRecord.id)
                    .where(OrderRecord.state == OrderState.SUBMITTING)
                    .limit(1)
                )
                is not None
            )

    def finish_order_submission(
        self,
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
        with self._sessions.begin() as session:
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
            return self._stored_order(
                record, session.get(OrderDraftRecord, record.draft_id)
            )

    def list_accounts(self) -> list[StoredAccount]:
        with self._sessions() as session:
            records = session.scalars(
                select(AccountRecord).order_by(AccountRecord.label)
            )
            return [self._stored_account(record) for record in records]

    def list_positions(self, account_id: str) -> list[StoredPosition] | None:
        with self._sessions() as session:
            account = session.get(AccountRecord, account_id)
            if account is None:
                return None
            records = session.scalars(
                select(PositionRecord)
                .where(PositionRecord.account_id == account_id)
                .order_by(PositionRecord.symbol)
            )
            return [self._stored_position(record, account) for record in records]

    def account_detail(self, account_id: str) -> AccountDetail | None:
        with self._sessions() as session:
            account = session.get(AccountRecord, account_id)
            if account is None:
                return None
            records = list(
                session.scalars(
                    select(PositionRecord)
                    .where(PositionRecord.account_id == account_id)
                    .order_by(PositionRecord.symbol)
                )
            )
            positions = tuple(
                self._stored_position(record, account) for record in records
            )
            return AccountDetail(
                account=Account(
                    id=account.id,
                    provider=account.provider,
                    label=account.label,
                    account_type=account.account_type,
                    currency=account.currency,
                ),
                refreshed_at=account.refreshed_at,
                as_of=positions[0].as_of if positions else None,
                positions=positions,
            )

    def account_capability(self, account_id: str) -> AccountCapabilities | None:
        with self._sessions() as session:
            account = session.get(AccountRecord, account_id)
            if account is None:
                return None
            record = session.get(AccountCapabilityRecord, account_id)
            if record is not None and not record.is_current:
                return None
            return (
                self._capability(record, account)
                if record is not None
                else self._unknown_capability(account)
            )

    def current_capabilities(self) -> list[AccountCapabilities]:
        with self._sessions() as session:
            records = session.execute(
                select(AccountRecord, AccountCapabilityRecord)
                .outerjoin(
                    AccountCapabilityRecord,
                    AccountCapabilityRecord.account_id == AccountRecord.id,
                )
                .where(
                    AccountCapabilityRecord.is_current.is_(True)
                    | AccountCapabilityRecord.account_id.is_(None)
                )
                .order_by(AccountRecord.label)
            ).all()
            return [
                self._capability(capability, account)
                if capability is not None
                else self._unknown_capability(account)
                for account, capability in records
            ]

    def provider_health(self) -> list[ProviderHealth]:
        with self._sessions() as session:
            accounts = list(
                session.scalars(select(AccountRecord).order_by(AccountRecord.provider))
            )
            accounts_by_provider: dict[str, list[AccountRecord]] = {}
            for account in accounts:
                accounts_by_provider.setdefault(account.provider, []).append(account)
            latest = session.scalar(
                select(RefreshRunRecord).order_by(RefreshRunRecord.id.desc()).limit(1)
            )
            outcomes = (
                {
                    item.provider: item
                    for item in self._outcomes_for_refresh(session, latest.id)
                }
                if latest is not None
                else {}
            )
            refresh_status = latest.status if latest is not None else None
            error_code = latest.error_code if latest is not None else None
            observed_at = latest.completed_at if latest is not None else None
            health = []
            for provider in sorted(set(accounts_by_provider) | set(outcomes)):
                state = self._provider_health_state(
                    outcome=outcomes.get(provider),
                    accounts=accounts_by_provider.get(provider, []),
                    refresh_status=refresh_status,
                    error_code=error_code,
                )
                health.append(
                    ProviderHealth(
                        provider=provider,
                        state=state,
                        observed_at=observed_at,
                        last_success_at=self._latest_provider_success(
                            session, provider
                        ),
                        blocks=self._health_blocks(state),
                    )
                )
            return health

    def save_refresh(
        self,
        snapshots: list[HoldingsSnapshot],
        started_at: datetime,
        completed_at: datetime,
        snapshot_date: date,
        failed_accounts: list[Account] | None = None,
        transactions: list[Transaction] | None = None,
        capabilities: list[AccountCapabilities] | None = None,
        failed_capability_accounts: list[Account] | None = None,
        current_accounts: list[Account] | None = None,
    ) -> "RefreshResult":
        failed_accounts = failed_accounts or []
        current_accounts = current_accounts or [
            snapshot.account for snapshot in snapshots
        ]
        with self._sessions.begin() as session:
            self._mark_removed_capabilities(session, current_accounts)
            for snapshot in snapshots:
                self._save_snapshot(session, snapshot, completed_at, snapshot_date)
            self._save_transactions(session, transactions or [], completed_at)
            self._save_capabilities(session, capabilities or [])
            self._mark_capabilities_stale(session, failed_capability_accounts or [])

            stale_account_ids = self._mark_failed_accounts(session, failed_accounts)
            outcomes = self._provider_outcomes(
                snapshots, failed_accounts, stale_account_ids
            )
            status = self._refresh_status(snapshots, failed_accounts)

            run = RefreshRunRecord(
                started_at=started_at,
                completed_at=completed_at,
                status=status,
                account_count=len(snapshots),
                position_count=sum(len(snapshot.positions) for snapshot in snapshots),
                snapshot_count=sum(
                    any(
                        position.market_value is not None
                        for position in snapshot.positions
                    )
                    for snapshot in snapshots
                ),
            )
            session.add(run)
            session.flush()
            self._save_outcomes(session, run.id, outcomes)
            return self._refresh_result(run, outcomes)

    def save_failed_refresh(
        self,
        started_at: datetime,
        completed_at: datetime,
        error_code: str,
        error_message: str,
    ) -> RefreshResult:
        with self._sessions.begin() as session:
            stale_accounts = self._mark_all_accounts_stale(session)
            self._mark_all_capabilities_stale(session)
            outcomes = self._failed_outcomes(stale_accounts)
            run = RefreshRunRecord(
                started_at=started_at,
                completed_at=completed_at,
                status="failed",
                account_count=0,
                position_count=0,
                snapshot_count=0,
                error_code=error_code,
                error_message=error_message,
            )
            session.add(run)
            session.flush()
            self._save_outcomes(session, run.id, outcomes)
            return self._refresh_result(run, outcomes)

    def daily_values(self, account_id: str) -> list[DailyAccountValue] | None:
        with self._sessions() as session:
            if session.get(AccountRecord, account_id) is None:
                return None
            records = session.scalars(
                select(DailyAccountValueRecord)
                .where(DailyAccountValueRecord.account_id == account_id)
                .order_by(DailyAccountValueRecord.snapshot_date)
            )
            return [
                DailyAccountValue(
                    account_id=record.account_id,
                    snapshot_date=record.snapshot_date,
                    value=record.value,
                    currency=record.currency,
                    recorded_at=record.recorded_at,
                )
                for record in records
            ]

    def all_positions(self) -> list[StoredPosition]:
        with self._sessions() as session:
            records = session.execute(
                select(PositionRecord, AccountRecord)
                .join(AccountRecord, PositionRecord.account_id == AccountRecord.id)
                .order_by(PositionRecord.account_id, PositionRecord.symbol)
            ).all()
            return [
                self._stored_position(pos_record, acc_record)
                for pos_record, acc_record in records
            ]

    def all_daily_values(self) -> list[DailyAccountValue]:
        with self._sessions() as session:
            records = session.scalars(
                select(DailyAccountValueRecord).order_by(
                    DailyAccountValueRecord.snapshot_date,
                    DailyAccountValueRecord.account_id,
                )
            )
            return [
                DailyAccountValue(
                    account_id=record.account_id,
                    snapshot_date=record.snapshot_date,
                    value=record.value,
                    currency=record.currency,
                    recorded_at=record.recorded_at,
                )
                for record in records
            ]

    def latest_refresh(self) -> RefreshResult | None:
        with self._sessions() as session:
            record = session.scalar(
                select(RefreshRunRecord).order_by(RefreshRunRecord.id.desc()).limit(1)
            )
            if record is None:
                return None
            return self._refresh_result(
                record, self._outcomes_for_refresh(session, record.id)
            )

    def list_activities(
        self,
        *,
        account_id: str | None = None,
        provider: str | None = None,
        transaction_type: str | None = None,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list["Activity"], int]:
        with self._sessions() as session:
            query = select(ActivityRecord, AccountRecord).join(
                AccountRecord, ActivityRecord.account_id == AccountRecord.id
            )
            if account_id is not None:
                query = query.where(ActivityRecord.account_id == account_id)
            if provider is not None:
                query = query.where(AccountRecord.provider == provider)
            if transaction_type is not None:
                query = query.where(ActivityRecord.transaction_type == transaction_type)
            if symbol is not None:
                query = query.where(ActivityRecord.symbol == symbol)
            if start_date is not None:
                query = query.where(ActivityRecord.occurred_on >= start_date)
            if end_date is not None:
                query = query.where(ActivityRecord.occurred_on <= end_date)

            total = len(session.execute(query).all())
            records = session.execute(
                query.order_by(
                    ActivityRecord.occurred_on.desc(),
                    ActivityRecord.occurred_at.desc(),
                    ActivityRecord.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [
                self._activity(activity, account) for activity, account in records
            ], total

    def _capability(
        self, record: AccountCapabilityRecord, account: AccountRecord
    ) -> AccountCapabilities:
        return AccountCapabilities(
            account_id=record.account_id,
            provider=record.provider,
            asset_classes=tuple(json.loads(record.asset_classes)),
            supported_sides=tuple(json.loads(record.supported_sides)),
            order_types=tuple(json.loads(record.order_types)),
            time_in_force=tuple(json.loads(record.time_in_force)),
            sizing_modes=tuple(json.loads(record.sizing_modes)),
            preview_supported=record.preview_supported,
            cancellation_supported=record.cancellation_supported,
            observed_at=record.observed_at,
            last_success_at=record.last_success_at,
            source=record.source,
            blocks=tuple(
                CapabilityBlock(**block) for block in json.loads(record.blocks)
            ),
            is_stale=(
                record.is_stale
                or account.is_stale
                or record.observed_at is None
                or record.observed_at < self._now() - timedelta(days=1)
            ),
        )

    def _unknown_capability(self, account: AccountRecord) -> AccountCapabilities:
        return AccountCapabilities(
            account_id=account.id,
            provider=account.provider,
            asset_classes=(),
            supported_sides=(),
            order_types=(),
            time_in_force=(),
            sizing_modes=(),
            preview_supported=False,
            cancellation_supported=False,
            observed_at=None,
            last_success_at=None,
            source="not_observed",
            blocks=(
                CapabilityBlock("capability_unknown", "Trading capability is unknown."),
            ),
            is_stale=True,
        )

    def _save_capabilities(
        self, session: Session, capabilities: list[AccountCapabilities]
    ) -> None:
        for capability in capabilities:
            if session.get(AccountRecord, capability.account_id) is None:
                continue
            record = session.get(AccountCapabilityRecord, capability.account_id)
            if record is None:
                record = AccountCapabilityRecord(account_id=capability.account_id)
                session.add(record)
            record.provider = capability.provider
            record.asset_classes = json.dumps(capability.asset_classes)
            record.supported_sides = json.dumps(capability.supported_sides)
            record.order_types = json.dumps(capability.order_types)
            record.time_in_force = json.dumps(capability.time_in_force)
            record.sizing_modes = json.dumps(capability.sizing_modes)
            record.preview_supported = capability.preview_supported
            record.cancellation_supported = capability.cancellation_supported
            record.observed_at = capability.observed_at
            record.last_success_at = capability.last_success_at
            record.source = capability.source
            record.blocks = json.dumps([block.to_dict() for block in capability.blocks])
            record.is_stale = capability.is_stale
            record.is_current = True

    def _mark_removed_capabilities(
        self, session: Session, current_accounts: list[Account]
    ) -> None:
        current_ids = {account.id for account in current_accounts}
        for record in session.scalars(select(AccountCapabilityRecord)):
            if record.account_id not in current_ids:
                record.is_current = False
                record.is_stale = True

    def _mark_capabilities_stale(
        self, session: Session, accounts: list[Account]
    ) -> None:
        for account in accounts:
            record = session.get(AccountCapabilityRecord, account.id)
            if record is not None:
                record.is_stale = True

    def _mark_all_capabilities_stale(self, session: Session) -> None:
        for record in session.scalars(select(AccountCapabilityRecord)):
            record.is_stale = True

    def _provider_health_state(
        self,
        *,
        outcome: ProviderRefreshOutcome | None,
        accounts: list[AccountRecord],
        refresh_status: str | None,
        error_code: str | None,
    ) -> ProviderHealthState:
        if outcome is None:
            return "unknown"
        if refresh_status == "failed":
            if error_code == "authentication_required":
                return "authentication_required"
            if error_code == "authorization_required":
                return "authorization_required"
            return "unavailable"
        if outcome.status == "success" and not any(
            account.is_stale for account in accounts
        ):
            return "healthy"
        return "degraded"

    def _health_blocks(self, state: ProviderHealthState) -> tuple[CapabilityBlock, ...]:
        if state == "authentication_required":
            return (
                CapabilityBlock(
                    "authentication_required",
                    "Provider authentication is required.",
                    "reconnect_provider",
                ),
            )
        if state == "authorization_required":
            return (
                CapabilityBlock(
                    "authorization_required",
                    "Provider authorization is required.",
                    "reconnect_provider",
                ),
            )
        if state == "unavailable":
            return (
                CapabilityBlock(
                    "provider_unavailable",
                    "Provider status is unavailable. Refresh again later.",
                ),
            )
        return ()

    def _latest_provider_success(
        self, session: Session, provider: str
    ) -> datetime | None:
        outcome = session.scalar(
            select(RefreshProviderOutcomeRecord)
            .where(
                RefreshProviderOutcomeRecord.provider == provider,
                RefreshProviderOutcomeRecord.status == "success",
            )
            .order_by(RefreshProviderOutcomeRecord.id.desc())
            .limit(1)
        )
        if outcome is None:
            return None
        run = session.get(RefreshRunRecord, outcome.refresh_id)
        return run.completed_at if run is not None else None

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)

    def _order_draft(self, record: OrderDraftRecord) -> "OrderDraft":
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

    def _stored_trading_settings(
        self, record: TradingSettingsRecord
    ) -> StoredTradingSettings:
        return StoredTradingSettings(
            live_trading_enabled=record.live_trading_enabled,
            kill_switch_active=record.kill_switch_active,
            max_order_shares=record.max_order_shares,
            max_order_notional_usd=record.max_order_notional_usd,
            updated_at=record.updated_at,
            version=record.version,
        )

    def _stored_account(self, record: AccountRecord) -> StoredAccount:
        return StoredAccount(
            account=Account(
                id=record.id,
                provider=record.provider,
                label=record.label,
                account_type=record.account_type,
                currency=record.currency,
            ),
            is_stale=record.is_stale,
            source_refreshed_at=record.refreshed_at,
        )

    def _stored_order(
        self, record: OrderRecord, draft: OrderDraftRecord | None = None
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
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
            version=record.version,
        )

    def _stored_authorization(
        self, record: OrderAuthorizationRecord
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

    def _save_snapshot(
        self,
        session: Session,
        snapshot: HoldingsSnapshot,
        refreshed_at: datetime,
        snapshot_date: date,
    ) -> None:
        account = session.get(AccountRecord, snapshot.account.id)
        if account is None:
            account = AccountRecord(id=snapshot.account.id)
            session.add(account)
        account.provider = snapshot.account.provider
        account.label = snapshot.account.label
        account.account_type = snapshot.account.account_type
        account.currency = snapshot.account.currency
        account.refreshed_at = refreshed_at
        account.is_stale = False

        session.execute(
            delete(PositionRecord).where(
                PositionRecord.account_id == snapshot.account.id
            )
        )
        session.add_all(
            [
                PositionRecord(
                    account_id=position.account_id,
                    symbol=position.symbol,
                    name=position.name,
                    asset_class=position.asset_class,
                    quantity=position.quantity,
                    current_price=position.current_price,
                    market_value=position.market_value,
                    cost_basis=position.cost_basis,
                    currency=position.currency,
                    as_of=snapshot.as_of,
                )
                for position in snapshot.positions
            ]
        )

        values = [
            position.market_value
            for position in snapshot.positions
            if position.market_value is not None
            and position.currency == snapshot.account.currency
        ]
        if not values:
            return

        daily_value = session.scalar(
            select(DailyAccountValueRecord).where(
                DailyAccountValueRecord.account_id == snapshot.account.id,
                DailyAccountValueRecord.snapshot_date == snapshot_date,
            )
        )
        if daily_value is None:
            daily_value = DailyAccountValueRecord(
                account_id=snapshot.account.id,
                snapshot_date=snapshot_date,
            )
            session.add(daily_value)
        daily_value.value = sum(values, start=Decimal("0"))
        daily_value.currency = snapshot.account.currency
        daily_value.recorded_at = refreshed_at

    def _mark_failed_accounts(
        self, session: Session, failed_accounts: list[Account]
    ) -> set[str]:
        stale_account_ids: set[str] = set()
        for account in failed_accounts:
            record = session.get(AccountRecord, account.id)
            if record is None:
                continue
            record.is_stale = True
            stale_account_ids.add(account.id)
        return stale_account_ids

    def _refresh_status(
        self,
        snapshots: list[HoldingsSnapshot],
        failed_accounts: list[Account],
    ) -> str:
        if not failed_accounts:
            return "success"
        if snapshots:
            return "partial"
        return "failed"

    def _mark_all_accounts_stale(self, session: Session) -> list[AccountRecord]:
        records = list(session.scalars(select(AccountRecord)))
        for record in records:
            record.is_stale = True
        return records

    def _provider_outcomes(
        self,
        snapshots: list[HoldingsSnapshot],
        failed_accounts: list[Account],
        stale_account_ids: set[str],
    ) -> tuple[ProviderRefreshOutcome, ...]:
        counts: dict[str, dict[str, int]] = {}
        for snapshot in snapshots:
            provider = counts.setdefault(
                snapshot.account.provider,
                {"refreshed": 0, "stale": 0, "excluded": 0},
            )
            provider["refreshed"] += 1
        for account in failed_accounts:
            provider = counts.setdefault(
                account.provider, {"refreshed": 0, "stale": 0, "excluded": 0}
            )
            key = "stale" if account.id in stale_account_ids else "excluded"
            provider[key] += 1

        outcomes: list[ProviderRefreshOutcome] = []
        for provider, count in sorted(counts.items()):
            has_failures = count["stale"] or count["excluded"]
            status = (
                "partial"
                if count["refreshed"] and has_failures
                else "failed"
                if has_failures
                else "success"
            )
            outcomes.append(
                ProviderRefreshOutcome(
                    provider=provider,
                    status=status,
                    accounts_refreshed=count["refreshed"],
                    stale_accounts=count["stale"],
                    excluded_accounts=count["excluded"],
                    warning=self._outcome_warning(
                        provider, count["stale"], count["excluded"]
                    ),
                )
            )
        return tuple(outcomes)

    def _failed_outcomes(
        self, stale_accounts: list[AccountRecord]
    ) -> tuple[ProviderRefreshOutcome, ...]:
        if not stale_accounts:
            return (
                ProviderRefreshOutcome(
                    provider="Portfolio provider",
                    status="failed",
                    accounts_refreshed=0,
                    stale_accounts=0,
                    excluded_accounts=0,
                    warning="Portfolio data is unavailable and excluded from totals.",
                ),
            )
        counts: dict[str, int] = {}
        for account in stale_accounts:
            counts[account.provider] = counts.get(account.provider, 0) + 1
        return tuple(
            ProviderRefreshOutcome(
                provider=provider,
                status="failed",
                accounts_refreshed=0,
                stale_accounts=count,
                excluded_accounts=0,
                warning=f"{provider} data is stale; last successful data is shown.",
            )
            for provider, count in sorted(counts.items())
        )

    def _outcome_warning(
        self, provider: str, stale_accounts: int, excluded_accounts: int
    ) -> str | None:
        if stale_accounts and excluded_accounts:
            return (
                f"{provider} has stale data and unavailable accounts excluded from "
                "totals."
            )
        if stale_accounts:
            return f"{provider} data is stale; last successful data is shown."
        if excluded_accounts:
            return f"{provider} data is unavailable and excluded from totals."
        return None

    def _save_outcomes(
        self,
        session: Session,
        refresh_id: int,
        outcomes: tuple[ProviderRefreshOutcome, ...],
    ) -> None:
        session.add_all(
            [
                RefreshProviderOutcomeRecord(
                    refresh_id=refresh_id,
                    provider=outcome.provider,
                    status=outcome.status,
                    accounts_refreshed=outcome.accounts_refreshed,
                    stale_accounts=outcome.stale_accounts,
                    excluded_accounts=outcome.excluded_accounts,
                    warning=outcome.warning,
                )
                for outcome in outcomes
            ]
        )

    def _outcomes_for_refresh(
        self, session: Session, refresh_id: int
    ) -> tuple[ProviderRefreshOutcome, ...]:
        records = session.scalars(
            select(RefreshProviderOutcomeRecord)
            .where(RefreshProviderOutcomeRecord.refresh_id == refresh_id)
            .order_by(RefreshProviderOutcomeRecord.provider)
        )
        return tuple(
            ProviderRefreshOutcome(
                provider=record.provider,
                status=record.status,
                accounts_refreshed=record.accounts_refreshed,
                stale_accounts=record.stale_accounts,
                excluded_accounts=record.excluded_accounts,
                warning=record.warning,
            )
            for record in records
        )

    def _save_transactions(
        self, session: Session, transactions: list[Transaction], imported_at: datetime
    ) -> None:
        for transaction in transactions:
            record = session.scalar(
                select(ActivityRecord).where(
                    ActivityRecord.account_id == transaction.account_id,
                    ActivityRecord.provider_transaction_id == transaction.id,
                )
            )
            if record is None:
                record = ActivityRecord(
                    account_id=transaction.account_id,
                    provider_transaction_id=transaction.id,
                    imported_at=imported_at,
                )
                session.add(record)
            record.occurred_on = transaction.occurred_on
            record.occurred_at = transaction.occurred_at
            record.transaction_type = transaction.transaction_type
            record.symbol = transaction.symbol
            record.description = transaction.description
            record.quantity = transaction.quantity
            record.amount = transaction.amount
            record.fees = transaction.fees
            record.currency = transaction.currency

    def _stored_position(
        self, record: PositionRecord, account: AccountRecord
    ) -> StoredPosition:
        return StoredPosition(
            position=Position(
                account_id=record.account_id,
                symbol=record.symbol,
                name=record.name,
                asset_class=record.asset_class,
                quantity=record.quantity,
                current_price=record.current_price,
                market_value=record.market_value,
                cost_basis=record.cost_basis,
                currency=record.currency,
            ),
            as_of=record.as_of,
            is_stale=account.is_stale,
            source_refreshed_at=account.refreshed_at,
        )

    def _refresh_result(
        self,
        record: RefreshRunRecord,
        outcomes: tuple[ProviderRefreshOutcome, ...] = (),
    ) -> RefreshResult:
        return RefreshResult(
            id=record.id,
            status=record.status,
            started_at=record.started_at,
            completed_at=record.completed_at,
            account_count=record.account_count,
            position_count=record.position_count,
            snapshot_count=record.snapshot_count,
            error_code=record.error_code,
            error_message=record.error_message,
            provider_outcomes=outcomes,
        )

    def _activity(self, record: ActivityRecord, account: AccountRecord) -> "Activity":
        return Activity(
            id=record.id,
            account_id=account.id,
            account_label=account.label,
            provider=account.provider,
            occurred_on=record.occurred_on,
            occurred_at=record.occurred_at,
            transaction_type=record.transaction_type,
            symbol=record.symbol,
            description=record.description,
            quantity=record.quantity,
            amount=record.amount,
            fees=record.fees,
            currency=record.currency,
            imported_at=record.imported_at,
        )


@dataclass(frozen=True)
class Activity:
    id: int
    account_id: str
    account_label: str
    provider: str
    occurred_on: date
    occurred_at: datetime | None
    transaction_type: str
    symbol: str | None
    description: str
    quantity: Decimal | None
    amount: Decimal
    fees: Decimal
    currency: str
    imported_at: datetime

    def to_dict(self) -> dict[str, int | str | None | dict[str, str]]:
        return {
            "id": self.id,
            "account": {"id": self.account_id, "label": self.account_label},
            "provider": self.provider,
            "occurred_on": self.occurred_on.isoformat(),
            "occurred_at": (
                self.occurred_at.isoformat() if self.occurred_at is not None else None
            ),
            "type": self.transaction_type,
            "symbol": self.symbol,
            "description": self.description,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "amount": str(self.amount),
            "fees": str(self.fees),
            "currency": self.currency,
            "imported_at": self.imported_at.isoformat(),
        }


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
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "version": self.version,
        }


@dataclass(frozen=True)
class ReconciliationClaim:
    order: StoredOrder
    attempt_id: UUID


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
