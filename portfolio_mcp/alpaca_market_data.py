import asyncio
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from portfolio_mcp.config import AlpacaMarketDataSettings
from portfolio_mcp.models import Instrument, Quote
from portfolio_mcp.provider import (
    InstrumentNotFoundError,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from portfolio_mcp.ticker import IntradayBar, IntradayHistory

NY = ZoneInfo("America/New_York")


class AlpacaHttpClient(Protocol):
    def request(self, url: str, headers: dict[str, str]) -> tuple[int, object]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibAlpacaHttpClient:
    def request(self, url: str, headers: dict[str, str]) -> tuple[int, object]:
        try:
            with build_opener(_NoRedirect()).open(
                Request(url, headers=headers), timeout=15
            ) as response:
                return response.status, json.loads(response.read(), parse_float=str)
        except HTTPError as error:
            return error.code, None
        except (URLError, TimeoutError, OSError):
            raise ProviderUnavailableError(
                "Alpaca market data is unavailable"
            ) from None
        except ValueError:
            raise ProviderResponseError("Alpaca returned an invalid response") from None


class AlpacaMarketDataProvider:
    source = "alpaca_market_data"

    def __init__(
        self,
        settings: AlpacaMarketDataSettings,
        *,
        http_client: AlpacaHttpClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._client = http_client or UrllibAlpacaHttpClient()
        self._headers = {
            "APCA-API-KEY-ID": settings.api_key,
            "APCA-API-SECRET-KEY": settings.secret_key,
            "Accept": "application/json",
        }
        self._clock = clock or (lambda: datetime.now(UTC))
        self._assets: list[Instrument] | None = None
        self._assets_at: datetime | None = None

    async def _get(self, url: str) -> object:
        status, body = await asyncio.to_thread(self._client.request, url, self._headers)
        if status == 404:
            raise InstrumentNotFoundError("Instrument not found")
        if status == 401:
            raise ProviderAuthenticationError("Unable to authenticate with Alpaca")
        if status == 403:
            raise ProviderAuthorizationError("Alpaca feed access is not authorized")
        if status == 429:
            raise ProviderRateLimitError("Alpaca rate limit reached; try again later")
        if status >= 500:
            raise ProviderUnavailableError("Alpaca market data is unavailable")
        if status != 200:
            raise ProviderResponseError("Alpaca returned an invalid response")
        return body

    async def _instruments(self) -> list[Instrument]:
        now = self._now()
        if (
            self._assets is not None
            and self._assets_at
            and now - self._assets_at < timedelta(hours=1)
        ):
            return self._assets
        body = await self._get(
            "https://api.alpaca.markets/v2/assets?status=active&asset_class=us_equity"
        )
        if not isinstance(body, list):
            raise ProviderResponseError("Alpaca returned invalid assets")
        assets = []
        for item in body:
            row = mapping(item)
            symbol = text(row.get("symbol"))
            assets.append(
                Instrument(
                    f"us-equity:{symbol}",
                    symbol,
                    text(row.get("name")),
                    "equity",
                    text(row.get("exchange")),
                    "USD",
                )
            )
        self._assets, self._assets_at = assets, now
        return assets

    async def search_instruments(self, query: str) -> list[Instrument]:
        query = query.strip().casefold()
        if not query:
            return []
        instruments = await self._instruments()
        return sorted(
            (
                item
                for item in instruments
                if query in item.symbol.casefold() or query in item.name.casefold()
            ),
            key=lambda item: (item.symbol.casefold() != query, item.symbol),
        )[:30]

    async def _instrument(self, instrument_id: str) -> Instrument:
        instrument = next(
            (item for item in await self._instruments() if item.id == instrument_id),
            None,
        )
        if instrument is None:
            raise InstrumentNotFoundError("Instrument not found")
        return instrument

    async def get_quote(self, instrument_id: str) -> Quote:
        instrument = await self._instrument(instrument_id)
        body = mapping(
            await self._get(
                "https://data.alpaca.markets/v2/stocks/"
                f"{quote(instrument.symbol, safe='')}/snapshot?feed=delayed_sip"
            )
        )
        if body.get("symbol") not in (None, instrument.symbol):
            raise ProviderResponseError("Alpaca returned a different instrument")
        latest_quote = mapping(body.get("latestQuote"))
        latest_trade = (
            mapping(body.get("latestTrade")) if body.get("latestTrade") else {}
        )
        observed = timestamp(latest_quote.get("t"))
        trade_time = timestamp(latest_trade.get("t")) if latest_trade else None
        cutoff = self._now() - timedelta(minutes=15)
        if observed > cutoff or (trade_time and trade_time > cutoff):
            raise ProviderResponseError(
                "Alpaca returned data newer than the delayed feed cutoff"
            )
        return Quote(
            instrument,
            self.source,
            observed,
            price(latest_trade.get("p")),
            price(latest_quote.get("bp")),
            price(latest_quote.get("ap")),
            "USD",
            "delayed_sip",
            900,
            trade_time,
        )

    async def get_execution_quote(self, instrument_id: str) -> Quote:
        instrument = await self._instrument(instrument_id)
        body = mapping(
            await self._get(
                "https://data.alpaca.markets/v2/stocks/"
                f"{quote(instrument.symbol, safe='')}/quotes/latest?feed=iex"
            )
        )
        if body.get("symbol") != instrument.symbol:
            raise ProviderResponseError("Alpaca returned a different instrument")
        latest = mapping(body.get("quote"))
        return Quote(
            instrument,
            "alpaca_iex_single_exchange",
            timestamp(latest.get("t")),
            None,
            price(latest.get("bp")),
            price(latest.get("ap")),
            "USD",
            "iex",
            0,
        )

    async def get_intraday(
        self, instrument_id: str, extended_hours: bool
    ) -> IntradayHistory:
        instrument = await self._instrument(instrument_id)
        now = self._now().astimezone(NY)
        calendar = await self._get(
            "https://api.alpaca.markets/v2/calendar?"
            + urlencode(
                {"start": now.date().isoformat(), "end": now.date().isoformat()}
            )
        )
        if not isinstance(calendar, list) or len(calendar) > 1:
            raise ProviderResponseError("Alpaca returned an invalid session calendar")
        opening, closing = time(9, 30), time(16)
        if calendar:
            session = mapping(calendar[0])
            try:
                if session.get("date") != now.date().isoformat():
                    raise ValueError("Unexpected session date")
                opening = time.fromisoformat(text(session.get("open")))
                closing = time.fromisoformat(text(session.get("close")))
                if opening >= closing or opening.tzinfo or closing.tzinfo:
                    raise ValueError("Invalid session bounds")
            except ValueError:
                raise ProviderResponseError(
                    "Alpaca returned invalid session times"
                ) from None
        start = datetime.combine(now.date(), time(4) if extended_hours else opening, NY)
        close = datetime.combine(
            now.date(), time(20) if extended_hours else closing, NY
        )
        cutoff = min(now - timedelta(minutes=15), close - timedelta(microseconds=1))
        bars: list[IntradayBar] = []
        if calendar and cutoff >= start:
            params = {
                "timeframe": "1Min",
                "feed": "sip",
                "start": start.isoformat(),
                "end": cutoff.isoformat(),
                "limit": "10000",
                "sort": "asc",
                "adjustment": "raw",
            }
            tokens: set[str] = set()
            while True:
                body = mapping(
                    await self._get(
                        "https://data.alpaca.markets/v2/stocks/"
                        f"{quote(instrument.symbol, safe='')}/bars?{urlencode(params)}"
                    )
                )
                raw_bars = body.get("bars")
                if raw_bars is None:
                    raw_bars = []
                if not isinstance(raw_bars, list):
                    raise ProviderResponseError("Alpaca returned invalid bars")
                for item in raw_bars:
                    row = mapping(item)
                    stamp = timestamp(row.get("t"))
                    if not start <= stamp <= cutoff:
                        raise ProviderResponseError(
                            "Alpaca returned a bar outside the requested session"
                        )
                    values = [number(row.get(key)) for key in ("o", "h", "l", "c", "v")]
                    if (
                        any(value < 0 for value in values)
                        or values[1] < max(values[:1] + values[2:4])
                        or values[2] > min(values[:2] + values[3:4])
                    ):
                        raise ProviderResponseError(
                            "Alpaca returned invalid OHLCV values"
                        )
                    bars.append(
                        IntradayBar(
                            stamp.isoformat(), *(str(value) for value in values)
                        )
                    )
                token = body.get("next_page_token")
                if not token:
                    break
                token = text(token)
                if token in tokens:
                    raise ProviderResponseError("Alpaca returned repeated pagination")
                tokens.add(token)
                params["page_token"] = token
        if any(
            timestamp(left.time) >= timestamp(right.time)
            for left, right in zip(bars, bars[1:])
        ):
            raise ProviderResponseError("Alpaca returned unordered bars")
        return IntradayHistory(
            now.date(),
            extended_hours,
            self.source,
            "delayed_sip",
            900,
            cutoff,
            tuple(bars),
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Clock must be timezone-aware")
        return now.astimezone(UTC)


def mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderResponseError("Alpaca returned an invalid response")
    return value


def text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderResponseError("Alpaca returned an invalid response")
    return value


def number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ProviderResponseError("Alpaca returned an invalid number")
    try:
        result = Decimal(str(value))
        if result.is_finite():
            return result
    except InvalidOperation:
        pass
    raise ProviderResponseError("Alpaca returned an invalid number")


def price(value: object) -> Decimal | None:
    if value is None:
        return None
    result = number(value)
    if result < 0:
        raise ProviderResponseError("Alpaca returned an invalid price")
    return result if result else None


def timestamp(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
        if result.tzinfo is not None:
            return result.astimezone(UTC)
    except ValueError:
        pass
    raise ProviderResponseError("Alpaca returned an invalid timestamp")
