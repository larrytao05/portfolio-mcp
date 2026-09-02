from dataclasses import dataclass
from datetime import date
from decimal import Decimal


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
