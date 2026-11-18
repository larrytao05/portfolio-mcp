import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import quote, urlencode

from portfolio_mcp.alpaca_market_data import AlpacaHttpClient, UrllibAlpacaHttpClient
from portfolio_mcp.config import AlpacaMarketDataSettings
from portfolio_mcp.provider import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)


@dataclass(frozen=True)
class AlpacaOrder:
    account_id: str
    id: str
    client_order_id: str | None
    symbol: str | None
    side: str | None
    order_type: str | None
    quantity: str | None
    filled_quantity: str | None
    filled_avg_price: str | None
    limit_price: str | None
    status: str
    submitted_at: str | None
    filled_at: str | None
    updated_at: str | None
    canceled_at: str | None
    expired_at: str | None
    replaced_at: str | None
    time_in_force: str | None = None


class AlpacaOrderReader:
    def __init__(
        self,
        account_id: str,
        upstream_account_id: str,
        settings: AlpacaMarketDataSettings,
        *,
        http_client: AlpacaHttpClient | None = None,
    ):
        if not account_id or not upstream_account_id:
            raise ValueError("Account IDs are required")
        self.account_id = account_id
        self._upstream_account_id = upstream_account_id
        self._client = http_client or UrllibAlpacaHttpClient()
        self._headers = {
            "APCA-API-KEY-ID": settings.api_key,
            "APCA-API-SECRET-KEY": settings.secret_key,
            "Accept": "application/json",
        }

    async def _get(self, path: str) -> object:
        status, body = await asyncio.to_thread(
            self._client.request, f"https://api.alpaca.markets/v2/{path}", self._headers
        )
        if status == 401:
            raise ProviderAuthenticationError("Unable to authenticate with Alpaca")
        if status == 403:
            raise ProviderAuthorizationError("Alpaca order access is not authorized")
        if status == 429:
            raise ProviderRateLimitError("Alpaca rate limit reached; try again later")
        if status >= 500:
            raise ProviderUnavailableError("Alpaca orders are unavailable")
        if status != 200:
            raise ProviderResponseError("Alpaca returned an invalid order response")
        return body

    async def _verify_account(self) -> None:
        account = await self._get("account")
        if (
            not isinstance(account, Mapping)
            or account.get("id") != self._upstream_account_id
        ):
            raise ProviderAuthorizationError(
                "Alpaca account does not match the configured route"
            )

    async def list_orders(
        self, symbol: str, status: Literal["open", "closed", "all"]
    ) -> tuple[AlpacaOrder, ...]:
        if not symbol or status not in {"open", "closed", "all"}:
            raise ValueError("A symbol and valid status are required")
        await self._verify_account()
        params = {
            "status": status,
            "symbols": symbol,
            "limit": "500",
            "direction": "desc",
            "nested": "false",
        }
        result: list[AlpacaOrder] = []
        cursors: set[str] = set()
        while True:
            body = await self._get(f"orders?{urlencode(params)}")
            if not isinstance(body, list) or len(body) > 500:
                raise ProviderResponseError("Alpaca returned invalid orders")
            page = [_parse_order(self.account_id, row) for row in body]
            if any(
                order.symbol is None or order.symbol.casefold() != symbol.casefold()
                for order in page
            ):
                raise ProviderResponseError(
                    "Alpaca returned an unexpected order symbol"
                )
            result.extend(page)
            if len(page) < 500:
                return tuple(result)
            cursor = page[-1].id
            if cursor in cursors:
                raise ProviderResponseError("Alpaca returned repeated order pagination")
            cursors.add(cursor)
            params["before_order_id"] = cursor

    async def get_order(self, order_id: str) -> AlpacaOrder:
        if not order_id:
            raise ValueError("Order ID is required")
        await self._verify_account()
        order = _parse_order(
            self.account_id, await self._get(f"orders/{quote(order_id, safe='')}")
        )
        if order.id != order_id:
            raise ProviderResponseError("Alpaca returned a different order")
        return order

    async def get_order_by_client_id(self, client_order_id: str) -> AlpacaOrder:
        if not client_order_id:
            raise ValueError("Client order ID is required")
        await self._verify_account()
        query = urlencode({"client_order_id": client_order_id})
        order = _parse_order(
            self.account_id, await self._get(f"orders:by_client_order_id?{query}")
        )
        if order.client_order_id != client_order_id:
            raise ProviderResponseError("Alpaca returned a different order")
        return order


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ProviderResponseError("Alpaca returned an invalid order field")
    return value


def _required_text(value: object) -> str:
    result = _optional_text(value)
    if result is None:
        raise ProviderResponseError("Alpaca returned an invalid order field")
    return result


def _decimal(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderResponseError(
            "Alpaca returned an invalid order quantity or price"
        )
    try:
        number = Decimal(value)
        if number.is_finite() and number >= 0:
            return value
    except InvalidOperation:
        pass
    raise ProviderResponseError("Alpaca returned an invalid order quantity or price")


def _parse_order(account_id: str, value: object) -> AlpacaOrder:
    if not isinstance(value, Mapping):
        raise ProviderResponseError("Alpaca returned an invalid order")
    return AlpacaOrder(
        account_id=account_id,
        id=_required_text(value.get("id")),
        client_order_id=_optional_text(value.get("client_order_id")),
        symbol=_optional_text(value.get("symbol")),
        side=_optional_text(value.get("side")),
        order_type=_optional_text(value.get("type")),
        quantity=_decimal(value.get("qty")),
        filled_quantity=_decimal(value.get("filled_qty")),
        filled_avg_price=_decimal(value.get("filled_avg_price")),
        limit_price=_decimal(value.get("limit_price")),
        status=_required_text(value.get("status")),
        submitted_at=_optional_text(value.get("submitted_at")),
        filled_at=_optional_text(value.get("filled_at")),
        updated_at=_optional_text(value.get("updated_at")),
        canceled_at=_optional_text(value.get("canceled_at")),
        expired_at=_optional_text(value.get("expired_at")),
        replaced_at=_optional_text(value.get("replaced_at")),
        time_in_force=_optional_text(value.get("time_in_force")),
    )
