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
    current_price: Decimal
    market_value: Decimal
    cost_basis: Decimal
    currency: str

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_class": self.asset_class,
            "quantity": str(self.quantity),
            "current_price": str(self.current_price),
            "market_value": str(self.market_value),
            "cost_basis": str(self.cost_basis),
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
