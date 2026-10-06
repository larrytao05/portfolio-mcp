from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from portfolio_mcp.models import (
    Account,
    Position,
)
from portfolio_mcp.schema import (
    AccountRecord,
    ActivityRecord,
    PositionRecord,
    RefreshRunRecord,
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


def _stored_account(record: AccountRecord) -> StoredAccount:
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


def _stored_position(record: PositionRecord, account: AccountRecord) -> StoredPosition:
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


def _activity(record: ActivityRecord, account: AccountRecord) -> "Activity":
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
class PortfolioView:
    latest_refresh: RefreshResult | None
    accounts: tuple[StoredAccount, ...]
    positions: tuple[StoredPosition, ...]
    daily_values: tuple[DailyAccountValue, ...]
