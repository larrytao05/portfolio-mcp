from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


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


class McpAuthorizationRecord(Base):
    __tablename__ = "mcp_authorizations"
    __table_args__ = (
        Index(
            "ix_mcp_authorizations_action_target_draft",
            "action",
            "target_draft_id",
        ),
        Index(
            "ix_mcp_authorizations_action_target_req",
            "action",
            "target_cancellation_request_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_draft_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("order_drafts.id"), nullable=True
    )
    target_cancellation_request_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cancellation_requests.id"), nullable=True
    )
    target_order_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("orders.id"), nullable=True
    )
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp(), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalidation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SchwabAccountMappingRecord(Base):
    __tablename__ = "schwab_account_mappings"
    __table_args__ = (
        Index("ix_schwab_account_mappings_account_id", "account_id", unique=True),
        Index("ix_schwab_account_mappings_hash", "schwab_account_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("accounts.id"), nullable=False, unique=True
    )
    schwab_account_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    masked_account_number: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)


class CancellationRequestRecord(Base):
    __tablename__ = "cancellation_requests"
    __table_args__ = (
        Index(
            "ix_cancellation_requests_order_status",
            "order_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders.id"), nullable=False
    )
    expected_order_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_order_state: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remaining_quantity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    invalidation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
            "'cancellation_requested', 'cancellation_result', "
            "'cancellation_request_created', 'cancellation_request_invalidated')",
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
