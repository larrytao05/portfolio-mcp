from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Account:
    id: str
    provider: str
    label: str
    account_type: str
    currency: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "provider": self.provider,
            "label": self.label,
            "account_type": self.account_type,
            "currency": self.currency,
        }


ProviderHealthState = Literal[
    "healthy",
    "degraded",
    "authentication_required",
    "authorization_required",
    "unavailable",
    "unknown",
]


@dataclass(frozen=True)
class CapabilityBlock:
    code: str
    message: str
    recovery_action: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "recovery_action": self.recovery_action,
        }


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    state: ProviderHealthState
    observed_at: datetime | None
    last_success_at: datetime | None
    blocks: tuple[CapabilityBlock, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "state": self.state,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "last_success_at": self.last_success_at.isoformat()
            if self.last_success_at
            else None,
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(frozen=True)
class AccountCapabilities:
    account_id: str
    provider: str
    asset_classes: tuple[str, ...]
    supported_sides: tuple[str, ...]
    order_types: tuple[str, ...]
    time_in_force: tuple[str, ...]
    sizing_modes: tuple[str, ...]
    preview_supported: bool
    cancellation_supported: bool
    observed_at: datetime | None
    last_success_at: datetime | None
    source: str
    blocks: tuple[CapabilityBlock, ...] = ()
    is_stale: bool = False

    @property
    def is_trade_capable(self) -> bool:
        return bool(
            self.observed_at
            and not self.is_stale
            and not self.blocks
            and self.asset_classes
            and self.supported_sides
            and self.order_types
            and self.time_in_force
            and self.sizing_modes
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "provider": self.provider,
            "asset_classes": list(self.asset_classes),
            "supported_sides": list(self.supported_sides),
            "order_types": list(self.order_types),
            "time_in_force": list(self.time_in_force),
            "sizing_modes": list(self.sizing_modes),
            "preview_supported": self.preview_supported,
            "cancellation_supported": self.cancellation_supported,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "last_success_at": self.last_success_at.isoformat()
            if self.last_success_at
            else None,
            "source": self.source,
            "blocks": [block.to_dict() for block in self.blocks],
            "is_stale": self.is_stale,
            "is_trade_capable": self.is_trade_capable,
        }


@dataclass(frozen=True)
class Position:
    account_id: str
    symbol: str
    name: str
    asset_class: str
    quantity: Decimal
    current_price: Decimal | None
    market_value: Decimal | None
    cost_basis: Decimal | None
    currency: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_class": self.asset_class,
            "quantity": str(self.quantity),
            "current_price": (
                str(self.current_price) if self.current_price is not None else None
            ),
            "market_value": (
                str(self.market_value) if self.market_value is not None else None
            ),
            "cost_basis": str(self.cost_basis) if self.cost_basis is not None else None,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class Transaction:
    id: str
    account_id: str
    occurred_on: date
    transaction_type: str
    symbol: str | None
    description: str
    quantity: Decimal | None
    amount: Decimal
    fees: Decimal
    currency: str
    occurred_at: datetime | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "date": self.occurred_on.isoformat(),
            "type": self.transaction_type,
            "symbol": self.symbol,
            "description": self.description,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "amount": str(self.amount),
            "fees": str(self.fees),
            "currency": self.currency,
            "occurred_at": (
                self.occurred_at.isoformat() if self.occurred_at is not None else None
            ),
        }


@dataclass(frozen=True)
class HoldingsSnapshot:
    account: Account
    as_of: date
    positions: tuple[Position, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "account": self.account.to_dict(),
            "as_of": self.as_of.isoformat(),
            "positions": [position.to_dict() for position in self.positions],
        }


@dataclass(frozen=True)
class TransactionHistory:
    account: Account
    start_date: date
    end_date: date
    transactions: tuple[Transaction, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "account": self.account.to_dict(),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "transactions": [
                transaction.to_dict() for transaction in self.transactions
            ],
        }


@dataclass(frozen=True)
class Instrument:
    """A broker-recognized instrument with a stable, provider-neutral identity."""

    id: str
    symbol: str
    name: str
    asset_class: str
    exchange: str | None
    currency: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "name": self.name,
            "asset_class": self.asset_class,
            "exchange": self.exchange,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class Quote:
    """A sourced observation of an instrument's available market prices."""

    instrument: Instrument
    source: str
    observed_at: datetime
    last_price: Decimal | None
    bid_price: Decimal | None
    ask_price: Decimal | None
    currency: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument.to_dict(),
            "source": self.source,
            "observed_at": self.observed_at.isoformat(),
            "last_price": str(self.last_price) if self.last_price is not None else None,
            "bid_price": str(self.bid_price) if self.bid_price is not None else None,
            "ask_price": str(self.ask_price) if self.ask_price is not None else None,
            "currency": self.currency,
        }
