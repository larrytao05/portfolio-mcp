import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

from portfolio_mcp.alpaca_market_data import AlpacaHttpClient, UrllibAlpacaHttpClient
from portfolio_mcp.provider import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from portfolio_mcp.schwab_transport import SchwabOAuthTransport


@dataclass(frozen=True)
class SchwabOrderLeg:
    account_id: str
    order_id: str
    leg_id: str | None
    symbol: str
    side: str | None
    order_type: str | None
    quantity: str | None
    filled_quantity: str | None
    limit_price: str | None
    status: str
    entered_at: str | None
    closed_at: str | None


@dataclass(frozen=True)
class SchwabOrderResult:
    orders: tuple[SchwabOrderLeg, ...]
    from_time: datetime
    to_time: datetime
    truncated: bool


class SchwabOrderReader:
    def __init__(
        self,
        account_id: str,
        encrypted_account_id: str,
        access_token: str,
        *,
        http_client: AlpacaHttpClient | None = None,
        transport: SchwabOAuthTransport | None = None,
    ):
        if not account_id or not encrypted_account_id or not access_token:
            raise ValueError("Explicit account mapping and authorization are required")
        self._transport = transport
        self.account_id = account_id
        self._encrypted_id = encrypted_account_id
        self._client = http_client or UrllibAlpacaHttpClient()
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

    async def list_orders(
        self, from_time: datetime, to_time: datetime, *, max_results: int = 3000
    ) -> SchwabOrderResult:
        if (
            from_time.tzinfo is None
            or to_time.tzinfo is None
            or not 1 <= max_results <= 3000
        ):
            raise ValueError("Invalid order query")
        start, end = from_time.astimezone(UTC), to_time.astimezone(UTC)
        if start >= end or end - start > timedelta(days=365):
            raise ValueError("Order range must be at most one year")
        params = {
            "fromEnteredTime": start.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "toEnteredTime": end.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "maxResults": str(max_results),
        }
        url = (
            "https://api.schwabapi.com/trader/v1/accounts/"
            f"{quote(self._encrypted_id, safe='')}/orders?{urlencode(params)}"
        )
        if self._transport is not None:
            status, body = await self._transport.request("GET", url)
        else:
            status, body = await asyncio.to_thread(
                self._client.request, url, self._headers
            )
        if status == 401:
            raise ProviderAuthenticationError("Unable to authenticate with Schwab")
        if status == 403:
            raise ProviderAuthorizationError("Schwab order access is not authorized")
        if status == 429:
            raise ProviderRateLimitError("Schwab rate limit reached; try again later")
        if status >= 500:
            raise ProviderUnavailableError("Schwab orders are unavailable")
        if status != 200 or not isinstance(body, list) or len(body) > max_results:
            raise ProviderResponseError("Schwab returned invalid orders")
        orders: list[SchwabOrderLeg] = []
        for item in body:
            orders.extend(_order_legs(self.account_id, item))
        return SchwabOrderResult(tuple(orders), start, end, len(body) == max_results)


def _text(value: object, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if isinstance(value, str) and value:
        return value
    raise ProviderResponseError("Schwab returned an invalid order field")


def _id(value: object, *, required: bool = False) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    return _text(value, required=required)


def _decimal(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ProviderResponseError("Schwab returned an invalid quantity or price")
    try:
        number = Decimal(str(value))
        if number.is_finite() and number >= 0:
            return str(value)
    except InvalidOperation:
        pass
    raise ProviderResponseError("Schwab returned an invalid quantity or price")


def _order_legs(account_id: str, value: object) -> list[SchwabOrderLeg]:
    if not isinstance(value, Mapping):
        raise ProviderResponseError("Schwab returned an invalid order")
    order_id = _id(value.get("orderId"), required=True)
    status = _text(value.get("status"), required=True)
    legs = value.get("orderLegCollection", [])
    children = value.get("childOrderStrategies", [])
    if (
        not isinstance(legs, list)
        or not isinstance(children, list)
        or order_id is None
        or status is None
    ):
        raise ProviderResponseError("Schwab returned invalid order legs")
    result = []
    for leg in legs:
        if not isinstance(leg, Mapping) or not isinstance(
            leg.get("instrument"), Mapping
        ):
            raise ProviderResponseError("Schwab returned invalid order legs")
        instrument = leg["instrument"]
        symbol = _text(instrument.get("symbol"), required=True)
        if symbol is None:
            raise ProviderResponseError("Schwab returned invalid order legs")
        result.append(
            SchwabOrderLeg(
                account_id,
                order_id,
                _id(leg.get("legId")),
                symbol,
                _text(leg.get("instruction")),
                _text(value.get("orderType")),
                _decimal(leg.get("quantity")),
                _decimal(value.get("filledQuantity")) if len(legs) == 1 else None,
                _decimal(value.get("price")) if len(legs) == 1 else None,
                status,
                _text(value.get("enteredTime")),
                _text(value.get("closeTime")),
            )
        )
    for child in children:
        result.extend(_order_legs(account_id, child))
    return result
