import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, overload
from zoneinfo import ZoneInfo

from portfolio_mcp.alpaca_market_data import AlpacaHttpClient, UrllibAlpacaHttpClient
from portfolio_mcp.alpaca_orders import AlpacaOrderReader
from portfolio_mcp.config import AlpacaMarketDataSettings
from portfolio_mcp.models import (
    Account,
    AccountCapabilities,
    CapabilityBlock,
    HoldingsSnapshot,
    Position,
    TransactionHistory,
)
from portfolio_mcp.provider import (
    AccountNotFoundError,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from portfolio_mcp.ticker import BrokerOrder, BrokerOrderHistory


class AlpacaPortfolioProvider:
    def __init__(
        self,
        settings: AlpacaMarketDataSettings,
        *,
        http_client: AlpacaHttpClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._client = http_client or UrllibAlpacaHttpClient()
        self._headers = {
            "APCA-API-KEY-ID": settings.api_key,
            "APCA-API-SECRET-KEY": settings.secret_key,
            "Accept": "application/json",
        }
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _get(self, path: str) -> object:
        status, body = await asyncio.to_thread(
            self._client.request, f"https://api.alpaca.markets/v2/{path}", self._headers
        )
        if status == 401:
            raise ProviderAuthenticationError("Unable to authenticate with Alpaca")
        if status == 403:
            raise ProviderAuthorizationError("Alpaca account access is not authorized")
        if status == 429:
            raise ProviderRateLimitError("Alpaca rate limit reached; try again later")
        if status >= 500:
            raise ProviderUnavailableError("Alpaca account data is unavailable")
        if status != 200:
            raise ProviderResponseError("Alpaca returned an invalid response")
        return body

    def execution_provider(
        self,
        account_id: str,
        upstream_account_id: str,
        *,
        dispatch_guard=None,
        clock=None,
    ):
        from portfolio_mcp.alpaca_execution import (
            AlpacaExecutionProvider,
            UrllibAlpacaExecutionTransport,
        )

        return AlpacaExecutionProvider(
            account_id,
            upstream_account_id,
            UrllibAlpacaExecutionTransport(self._settings),
            dispatch_guard=dispatch_guard,
            clock=clock,
        )

    async def list_accounts(self) -> list[Account]:
        body = _mapping(await self._get("account"))
        return [self._account(body)]

    async def get_account_capabilities(
        self, account_ids: list[str]
    ) -> list[AccountCapabilities]:
        body = _mapping(await self._get("account"))
        account_id = _text(body.get("id"))
        if account_ids != [account_id]:
            raise AccountNotFoundError("Account not found")
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Clock must be timezone-aware")
        now = now.astimezone(UTC)
        available = (
            body.get("status") == "ACTIVE"
            and body.get("trading_blocked") is False
            and body.get("account_blocked") is False
        )
        return [
            AccountCapabilities(
                account_id,
                "Alpaca",
                ("equity", "etf"),
                ("buy", "sell"),
                ("market", "limit"),
                ("day",),
                ("whole_shares",),
                False,
                available,
                now,
                now,
                "alpaca_account",
                ()
                if available
                else (
                    CapabilityBlock(
                        "capability_unavailable",
                        "Alpaca trading capability is unavailable.",
                    ),
                ),
                not available,
            )
        ]

    def _account(self, body: Mapping[str, object]) -> Account:
        return Account(
            _text(body.get("id")),
            "Alpaca",
            "Alpaca",
            "brokerage",
            _text(body.get("currency")),
        )

    async def get_broker_orders(
        self,
        account_id: str,
        symbol: str,
        status: Literal["open", "closed", "all"] = "all",
    ) -> BrokerOrderHistory:
        account = (await self.list_accounts())[0]
        if account.id != account_id:
            raise AccountNotFoundError("Account not found")
        reader = AlpacaOrderReader(
            account_id, account_id, self._settings, http_client=self._client
        )
        orders = await reader.list_orders(symbol, status)
        return BrokerOrderHistory(
            account,
            tuple(
                BrokerOrder(
                    o.id,
                    o.account_id,
                    o.symbol or "Unknown",
                    o.side or "Unknown",
                    o.order_type or "Unknown",
                    o.quantity,
                    o.filled_quantity,
                    o.status,
                    o.limit_price,
                    o.submitted_at,
                    o.filled_at,
                    client_order_id=o.client_order_id,
                    filled_avg_price=o.filled_avg_price,
                    updated_at=o.updated_at,
                    canceled_at=o.canceled_at,
                    expired_at=o.expired_at,
                    replaced_at=o.replaced_at,
                )
                for o in orders
            ),
            "open_and_executed",
            "live_request",
            "Orders retrieved from Alpaca.",
            lookback_days=None,
            source="Alpaca",
        )

    async def get_holdings(self, account_id: str) -> HoldingsSnapshot:
        account_data = _mapping(await self._get("account"))
        account = self._account(account_data)
        if account.id != account_id:
            raise AccountNotFoundError("Account not found")
        body = await self._get("positions")
        if not isinstance(body, list):
            raise ProviderResponseError("Alpaca returned invalid positions")
        positions = []
        for item in body:
            row = _mapping(item)
            symbol = _text(row.get("symbol"))
            positions.append(
                Position(
                    account_id=account.id,
                    symbol=symbol,
                    name=symbol,
                    asset_class=_asset_class(row.get("asset_class")),
                    quantity=_decimal(row.get("qty"), required=True),
                    current_price=_decimal(row.get("current_price")),
                    market_value=_decimal(row.get("market_value")),
                    cost_basis=_decimal(row.get("cost_basis")),
                    currency=account.currency,
                )
            )
        cash = _decimal(account_data.get("cash"))
        if cash is not None:
            positions.append(
                Position(
                    account.id,
                    "USD",
                    "Cash",
                    "cash",
                    cash,
                    Decimal("1"),
                    cash,
                    None,
                    account.currency,
                )
            )
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Clock must return a timezone-aware timestamp")
        return HoldingsSnapshot(
            account,
            now.astimezone(ZoneInfo("America/New_York")).date(),
            tuple(positions),
        )

    async def get_transactions(
        self, account_id: str, start_date: date, end_date: date
    ) -> TransactionHistory:
        raise ProviderUnavailableError("Alpaca transaction history is not supported")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderResponseError("Alpaca returned an invalid response")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError("Alpaca returned an invalid response")
    return value


@overload
def _decimal(value: object, *, required: Literal[True]) -> Decimal: ...


@overload
def _decimal(value: object, *, required: Literal[False] = False) -> Decimal | None: ...


def _decimal(value: object, *, required: bool = False) -> Decimal | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ProviderResponseError("Alpaca returned an invalid decimal")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ProviderResponseError("Alpaca returned an invalid decimal") from None
    if not result.is_finite():
        raise ProviderResponseError("Alpaca returned an invalid decimal")
    return result


def _asset_class(value: object) -> str:
    kind = _text(value)
    return {"us_equity": "equity", "crypto": "crypto"}.get(kind, "unsupported")
