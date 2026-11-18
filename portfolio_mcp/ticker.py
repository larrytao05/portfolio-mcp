from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Literal

from portfolio_mcp.models import Account


@dataclass(frozen=True)
class IntradayBar:
    time: str
    open: str
    high: str
    low: str
    close: str
    volume: str


@dataclass(frozen=True)
class IntradayHistory:
    session_date: date
    extended_hours: bool
    source: str
    feed: str
    delay_seconds: int
    available_through: datetime
    bars: tuple[IntradayBar, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "session_date": self.session_date.isoformat(),
            "available_through": self.available_through.isoformat(),
        }


@dataclass(frozen=True)
class BrokerOrder:
    id: str
    account_id: str
    symbol: str
    side: str
    order_type: str
    quantity: str | None
    filled_quantity: str | None
    status: str
    limit_price: str | None
    time_placed: str | None
    time_executed: str | None
    leg_id: str | None = None
    client_order_id: str | None = None
    filled_avg_price: str | None = None
    updated_at: str | None = None
    canceled_at: str | None = None
    expired_at: str | None = None
    replaced_at: str | None = None


@dataclass(frozen=True)
class BrokerOrderHistory:
    account: Account
    orders: tuple[BrokerOrder, ...]
    coverage: Literal["open_and_executed", "executed_only", "unsupported"]
    freshness: str
    warning: str
    synced_at: str | None = None
    lookback_days: int | None = 90
    source: str = "unknown"
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "account": self.account.to_dict()}
