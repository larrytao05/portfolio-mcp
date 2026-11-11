from datetime import UTC, datetime, timedelta
from typing import Literal

from portfolio_mcp.config import SchwabMarketDataSettings
from portfolio_mcp.models import Account
from portfolio_mcp.schwab_market_data import SchwabMarketDataProvider
from portfolio_mcp.schwab_orders import SchwabOrderReader
from portfolio_mcp.ticker import BrokerOrder, BrokerOrderHistory

CLOSED = {"REJECTED", "CANCELED", "REPLACED", "FILLED", "EXPIRED"}
OPEN = {
    "AWAITING_PARENT_ORDER",
    "AWAITING_CONDITION",
    "AWAITING_STOP_CONDITION",
    "AWAITING_MANUAL_REVIEW",
    "ACCEPTED",
    "AWAITING_UR_OUT",
    "PENDING_ACTIVATION",
    "QUEUED",
    "WORKING",
    "PENDING_CANCEL",
    "PENDING_REPLACE",
    "NEW",
    "AWAITING_RELEASE_TIME",
    "PENDING_ACKNOWLEDGEMENT",
    "PENDING_RECALL",
}


class DirectSchwabOrders:
    def __init__(self, settings: SchwabMarketDataSettings, bindings: dict[str, str]):
        self._auth = SchwabMarketDataProvider(settings)
        self._bindings = bindings

    def has_account(self, account_id: str) -> bool:
        return bool(self._bindings.get(account_id))

    async def get_orders(
        self, account: Account, symbol: str, status: Literal["open", "closed", "all"]
    ) -> BrokerOrderHistory:
        token = await self._auth._access_token()
        reader = SchwabOrderReader(account.id, self._bindings[account.id], token)
        now = datetime.now(UTC)
        result = await reader.list_orders(now - timedelta(days=90), now)
        unknown = any(item.status not in OPEN | CLOSED for item in result.orders)
        selected = [
            item
            for item in result.orders
            if item.symbol.casefold() == symbol.casefold()
            and (
                status == "all" or item.status in (OPEN if status == "open" else CLOSED)
            )
        ]
        warning = "Direct Schwab order history uses a 90-day query."
        if unknown:
            warning += (
                " Some orders have an unclassified status and are omitted "
                "from filtered views."
            )
        return BrokerOrderHistory(
            account,
            tuple(
                BrokerOrder(
                    item.order_id,
                    account.id,
                    item.symbol,
                    item.side or "Unknown",
                    item.order_type or "Unknown",
                    item.quantity,
                    item.filled_quantity,
                    item.status,
                    item.limit_price,
                    item.entered_at,
                    item.closed_at,
                    leg_id=item.leg_id,
                )
                for item in selected
            ),
            "open_and_executed",
            "live_request",
            warning,
            lookback_days=90,
            source="Schwab direct",
            truncated=result.truncated,
        )
