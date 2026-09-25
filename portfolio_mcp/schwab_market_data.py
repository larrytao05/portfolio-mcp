import asyncio
import base64
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from portfolio_mcp.config import SchwabMarketDataSettings
from portfolio_mcp.models import Instrument, Quote
from portfolio_mcp.provider import (
    InstrumentNotFoundError,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)


class SchwabHttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]: ...


class UrllibSchwabHttpClient:
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                return response.status, _decode_body(response.read())
        except HTTPError as error:
            return error.code, _decode_body(error.read())
        except URLError:
            raise ProviderUnavailableError(
                "Schwab market data is temporarily unavailable"
            ) from None


class SchwabMarketDataProvider:
    source = "schwab_market_data"
    _token_url = "https://api.schwabapi.com/v1/oauth/token"
    _market_data_url = "https://api.schwabapi.com/marketdata/v1"

    def __init__(
        self,
        settings: SchwabMarketDataSettings,
        *,
        http_client: SchwabHttpClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client or UrllibSchwabHttpClient()
        self._clock = clock or (lambda: datetime.now(UTC))

    def authorization_url(self) -> str:
        parameters = urlencode(
            {
                "client_id": self._settings.client_id,
                "response_type": "code",
                "redirect_uri": self._settings.callback_url,
            }
        )
        return f"https://api.schwabapi.com/v1/oauth/authorize?{parameters}"

    def authorization_code_from_redirect_url(self, redirect_url: str) -> str:
        redirect = urlparse(redirect_url)
        callback = urlparse(self._settings.callback_url)
        redirect_base = urlunparse(
            (redirect.scheme, redirect.netloc, redirect.path, "", "", "")
        ).rstrip("/")
        callback_base = urlunparse(
            (callback.scheme, callback.netloc, callback.path, "", "", "")
        ).rstrip("/")
        if redirect_base != callback_base:
            raise ProviderResponseError(
                "Authorization redirect did not match the configured callback URL"
            )

        code = parse_qs(redirect.query).get("code", [""])[0]
        if not code:
            raise ProviderResponseError("Authorization redirect did not include a code")
        return code

    async def exchange_authorization_code(self, code: str) -> str:
        status, response = await self._http_request(
            "POST",
            self._token_url,
            self._token_headers(),
            urlencode(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._settings.callback_url,
                }
            ).encode(),
        )
        _raise_for_status(status)
        return _required_string(_required_mapping(response).get("refresh_token"))

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
        body = urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._settings.refresh_token,
            }
        ).encode()
        status, response = await self._http_request(
            "POST",
            self._token_url,
            self._token_headers(),
            body,
        )
        if status == 400:
            raise ProviderAuthenticationError(
                "Refresh token was rejected by Schwab; run "
                "`uv run --env-file .env python -m portfolio_mcp.schwab_oauth` "
                "to replace it"
            )
        _raise_for_status(status)
        return _required_string(_required_mapping(response).get("access_token"))

    def _token_headers(self) -> dict[str, str]:
        credentials = f"{self._settings.client_id}:{self._settings.client_secret}"
        authorization = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {authorization}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

    async def _http_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        try:
            return await asyncio.to_thread(
                self._http_client.request,
                method,
                url,
                headers,
                body,
            )
        except TimeoutError:
            raise ProviderUnavailableError(
                "Schwab market data is temporarily unavailable"
            ) from None


def _decode_body(body: bytes) -> object:
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


def _raise_for_status(status: int) -> None:
    if 200 <= status < 300:
        return
    if status == 401:
        raise ProviderAuthenticationError("Unable to authenticate with Schwab")
    if status == 403:
        raise ProviderAuthorizationError("Schwab market data access is not authorized")
    if status == 429:
        raise ProviderRateLimitError(
            "Schwab market data rate limit reached; try again later"
        )
    if status <= 0 or status >= 500:
        raise ProviderUnavailableError("Schwab market data is temporarily unavailable")
    raise ProviderResponseError("Schwab market data returned an unexpected response")


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


def _asset_class(value: str) -> str:
    return {
        "EQUITY": "equity",
        "ETF": "etf",
        "MUTUAL_FUND": "mutual_fund",
        "OPTION": "option",
        "FIXED_INCOME": "fixed_income",
    }.get(value, value.casefold())


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
