from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote, urlencode

from portfolio_mcp.config import SchwabSettings
from portfolio_mcp.models import Instrument, Quote
from portfolio_mcp.provider import (
    InstrumentNotFoundError,
    ProviderResponseError,
)
from portfolio_mcp.schwab_transport import (
    SchwabHttpClient,
    SchwabOAuthTransport,
    UrllibSchwabHttpClient,
    decode_body,
    raise_for_status,
    schwab_asset_class,
)

_decode_body = decode_body
_asset_class = schwab_asset_class


class SchwabMarketDataProvider:
    source = "schwab_market_data"
    _token_url = "https://api.schwabapi.com/v1/oauth/token"
    _market_data_url = "https://api.schwabapi.com/marketdata/v1"

    def __init__(
        self,
        settings: SchwabSettings,
        *,
        http_client: SchwabHttpClient | None = None,
        clock: Callable[[], datetime] | None = None,
        transport: SchwabOAuthTransport | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client or UrllibSchwabHttpClient(
            context="Schwab market data"
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._transport = transport or SchwabOAuthTransport(
            settings,
            http_client=self._http_client,
            token_url=self._token_url,
            context="Schwab market data",
        )

    def authorization_url(self) -> str:
        return self._transport.authorization_url()

    def authorization_code_from_redirect_url(self, redirect_url: str) -> str:
        return self._transport.authorization_code_from_redirect_url(redirect_url)

    async def exchange_authorization_code(self, code: str) -> str:
        return await self._transport.exchange_authorization_code(code)

    async def search_instruments(self, query: str) -> list[Instrument]:
        normalized_query = query.strip()
        if not normalized_query:
            return []
        parameters = urlencode({"symbol": normalized_query, "projection": "search"})
        status, body = await self._request(
            "GET",
            f"{self._market_data_url}/instruments?{parameters}",
        )
        _raise_for_status(status)
        instruments = body.get("instruments") if isinstance(body, Mapping) else body
        if not isinstance(instruments, list):
            raise ProviderResponseError(
                "Schwab market data returned an unexpected response"
            )
        return [_instrument_from_search_result(item) for item in instruments]

    async def get_quote(self, instrument_id: str) -> Quote:
        asset_class, symbol = _instrument_id_parts(instrument_id)
        status, body = await self._request(
            "GET",
            f"{self._market_data_url}/{quote(symbol, safe='')}/quotes",
        )
        if status == 404:
            raise InstrumentNotFoundError("Instrument not found")
        _raise_for_status(status)
        source = _required_mapping(body, symbol)
        quote_data = _required_mapping(source.get("quote"))
        reference = _required_mapping(source.get("reference"))
        observed_at = _observation_time(quote_data.get("quoteTime"), self._clock)
        symbol = _optional_string(source.get("symbol")) or _required_string(
            reference.get("symbol")
        )
        instrument = Instrument(
            id=instrument_id,
            symbol=symbol,
            name=_optional_string(reference.get("description")) or symbol,
            asset_class=asset_class,
            exchange=_optional_string(reference.get("exchangeName")),
            currency=_optional_string(reference.get("currency")),
        )
        return Quote(
            instrument=instrument,
            source=self.source,
            observed_at=observed_at,
            last_price=_optional_decimal(quote_data.get("lastPrice")),
            bid_price=_optional_decimal(quote_data.get("bidPrice")),
            ask_price=_optional_decimal(quote_data.get("askPrice")),
            currency=instrument.currency,
        )

    async def _request(self, method: str, url: str) -> tuple[int, object]:
        token = await self._access_token()
        return await self._http_request(
            method,
            url,
            {"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def _access_token(self) -> str:
        return await self._transport.access_token()

    def _token_headers(self) -> dict[str, str]:
        return self._transport.token_headers()

    async def _http_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        return await self._transport.http_request(method, url, headers, body)


def _raise_for_status(status: int) -> None:
    raise_for_status(status, context="Schwab market data")


def _required_mapping(value: object, key: str | None = None) -> Mapping[str, object]:
    source = value
    if key is not None and isinstance(value, Mapping):
        source = value.get(key)
    if isinstance(source, Mapping):
        return source
    raise ProviderResponseError("Schwab market data returned an unexpected response")


def _required_string(value: object) -> str:
    if isinstance(value, str) and value:
        return value
    raise ProviderResponseError("Schwab market data returned an unexpected response")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        try:
            return Decimal(str(value))
        except ArithmeticError:
            pass
    raise ProviderResponseError("Schwab market data returned an unexpected response")


def _observation_time(value: object, clock: Callable[[], datetime]) -> datetime:
    if value is not None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ProviderResponseError(
                "Schwab market data returned an unexpected response"
            )
        try:
            return datetime.fromtimestamp(value / 1000, UTC)
        except (OverflowError, OSError, ValueError):
            raise ProviderResponseError(
                "Schwab market data returned an unexpected response"
            ) from None
    now = clock()
    if now.tzinfo is None:
        raise ValueError("Clock must return a timezone-aware timestamp")
    return now.astimezone(UTC)


def _instrument_id_parts(instrument_id: str) -> tuple[str, str]:
    prefix, separator, symbol = instrument_id.partition(":")
    asset_class = prefix.removeprefix("us-")
    if not separator or not symbol or not asset_class or asset_class == prefix:
        raise InstrumentNotFoundError("Instrument not found")
    return asset_class, symbol


def _instrument_from_search_result(source: object) -> Instrument:
    data = _required_mapping(source)
    symbol = _required_string(data.get("symbol"))
    asset_class = _asset_class(_required_string(data.get("assetType")))
    return Instrument(
        id=f"us-{asset_class}:{symbol}",
        symbol=symbol,
        name=_optional_string(data.get("description")) or symbol,
        asset_class=asset_class,
        exchange=_optional_string(data.get("exchange")),
        currency=_optional_string(data.get("currency")),
    )
