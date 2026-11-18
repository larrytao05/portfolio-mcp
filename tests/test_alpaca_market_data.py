from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from portfolio_mcp.alpaca_market_data import AlpacaMarketDataProvider
from portfolio_mcp.api import create_app
from portfolio_mcp.bootstrap import create_market_data_provider
from portfolio_mcp.config import AlpacaMarketDataSettings
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.provider import (
    ProviderAuthorizationError,
    ProviderConfigurationError,
    ProviderResponseError,
)

ASSET = {"symbol": "NVDA", "name": "NVIDIA", "exchange": "NASDAQ"}
BAR = {
    "t": "2026-11-10T14:30:00Z",
    "o": "200.0001",
    "h": "201",
    "l": "199",
    "c": "200.2",
    "v": 1000,
}


class Http:
    def __init__(self, responses, calendar=None):
        self.responses = iter(responses)
        self.urls = []
        self.calendar = calendar

    def request(self, url, headers):
        self.urls.append(url)
        if "/calendar?" in url:
            if self.calendar is not None:
                return 200, self.calendar
            day = parse_qs(urlparse(url).query)["start"][0]
            return 200, (
                [{"date": day, "open": "09:30", "close": "16:00"}]
                if datetime.fromisoformat(day).weekday() < 5
                else []
            )
        return next(self.responses)


def provider(http, now=None):
    return AlpacaMarketDataProvider(
        AlpacaMarketDataSettings("synthetic-key", "synthetic-secret"),
        http_client=http,
        clock=lambda: now or datetime(2026, 11, 10, 16, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_delayed_bars_are_exact_paginated_and_session_bounded():
    http = Http(
        [
            (200, [ASSET]),
            (200, {"bars": [BAR], "next_page_token": "next"}),
            (200, {"bars": [{**BAR, "t": "2026-11-10T14:31:00Z"}]}),
        ]
    )
    result = await provider(http).get_intraday("us-equity:NVDA", False)
    query = parse_qs(urlparse(http.urls[2]).query)
    assert query["feed"] == ["sip"]
    assert query["start"] == ["2026-11-10T09:30:00-05:00"]
    assert query["end"] == ["2026-11-10T10:45:00-05:00"]
    assert result.bars[0].open == "200.0001"
    assert len(result.bars) == 2
    assert "page_token=next" in http.urls[3]
    assert result.delay_seconds == 900


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now",
    [datetime(2026, 11, 10, 13, tzinfo=UTC), datetime(2026, 11, 8, 16, tzinfo=UTC)],
)
async def test_no_session_bars_before_open_or_weekend(now):
    http = Http([(200, [ASSET])])
    result = await provider(http, now).get_intraday("us-equity:NVDA", False)
    assert not result.bars
    assert len(http.urls) == 2


@pytest.mark.asyncio
async def test_regular_session_excludes_the_closing_minute_on_early_close():
    calendar = [{"date": "2026-11-27", "open": "09:30", "close": "13:00"}]
    http = Http([(200, [ASSET]), (200, {"bars": []})], calendar=calendar)
    result = await provider(http, datetime(2026, 11, 27, 22, tzinfo=UTC)).get_intraday(
        "us-equity:NVDA", False
    )
    assert result.available_through.isoformat() == "2026-11-27T12:59:59.999999-05:00"
    query = parse_qs(urlparse(http.urls[-1]).query)
    assert query["end"] == ["2026-11-27T12:59:59.999999-05:00"]
    http = Http(
        [(200, [ASSET]), (200, {"bars": [{**BAR, "t": "2026-11-27T18:00:00Z"}]})],
        calendar=calendar,
    )
    with pytest.raises(ProviderResponseError, match="outside"):
        await provider(http, datetime(2026, 11, 27, 22, tzinfo=UTC)).get_intraday(
            "us-equity:NVDA", False
        )


@pytest.mark.asyncio
async def test_holiday_has_no_bars_request():
    http = Http([(200, [ASSET])], calendar=[])
    result = await provider(http).get_intraday("us-equity:NVDA", False)
    assert not result.bars
    assert not any("/bars?" in url for url in http.urls)


@pytest.mark.asyncio
async def test_quote_uses_delayed_feed_and_actual_distinct_timestamps():
    http = Http(
        [
            (200, [ASSET]),
            (
                200,
                {
                    "symbol": "NVDA",
                    "latestQuote": {
                        "t": "2026-11-10T15:40:00Z",
                        "bp": "200.0100",
                        "ap": "200.02",
                    },
                    "latestTrade": {"t": "2026-11-10T15:39:00Z", "p": "200.015"},
                },
            ),
        ]
    )
    result = await provider(http).get_quote("us-equity:NVDA")
    assert "feed=delayed_sip" in http.urls[-1]
    assert str(result.bid_price) == "200.0100"
    assert result.observed_at.minute == 40
    assert result.trade_observed_at and result.trade_observed_at.minute == 39


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bar",
    [{**BAR, "c": "NaN"}, {**BAR, "t": "2026-11-10T16:00:00Z"}, {**BAR, "h": "100"}],
)
async def test_invalid_or_too_recent_bars_fail_closed(bar):
    with pytest.raises(ProviderResponseError):
        await provider(Http([(200, [ASSET]), (200, {"bars": [bar]})])).get_intraday(
            "us-equity:NVDA", False
        )


@pytest.mark.asyncio
async def test_feed_denial_is_explicit_and_does_not_fallback():
    http = Http([(200, [ASSET]), (403, {"message": "synthetic"})])
    with pytest.raises(ProviderAuthorizationError):
        await provider(http).get_quote("us-equity:NVDA")
    assert len(http.urls) == 2


def test_alpaca_is_live_default_and_missing_configuration_is_explicit():
    with pytest.raises(ProviderConfigurationError, match="ALPACA_API_KEY"):
        create_market_data_provider({})
    assert isinstance(
        create_market_data_provider(
            {"ALPACA_API_KEY": "synthetic", "ALPACA_SECRET_KEY": "synthetic"}
        ),
        AlpacaMarketDataProvider,
    )


def test_fixture_ticker_api_keeps_account_ownership_and_coverage(tmp_path):
    with TestClient(
        create_app(
            FixturePortfolioProvider(), database_url=f"sqlite:///{tmp_path}/test.sqlite"
        )
    ) as client:
        assert client.post("/api/connections/fixture").status_code == 200
        history = client.get("/api/instruments/us-equity:NVDA/intraday").json()[
            "history"
        ]
        assert len(history["bars"]) == 90
        assert history["feed"] == "fixture"
        for account in ("schwab-taxable-demo", "fidelity-roth-demo"):
            response = client.get(f"/api/accounts/{account}/broker-orders?symbol=NVDA")
            assert response.status_code == 200
            result = response.json()["history"]
            assert result["coverage"] == (
                "open_and_executed"
                if account == "schwab-taxable-demo"
                else "executed_only"
            )
            assert all(row["account_id"] == account for row in result["orders"])


def test_fixture_order_history_has_multiple_pages(tmp_path):
    with TestClient(
        create_app(
            FixturePortfolioProvider(), database_url=f"sqlite:///{tmp_path}/test.sqlite"
        )
    ) as client:
        assert client.post("/api/connections/fixture").status_code == 200
        history = client.get(
            "/api/accounts/schwab-taxable-demo/broker-orders?symbol=VTI&status=closed"
        ).json()["history"]
        assert len(history["orders"]) == 21
        assert len({order["id"] for order in history["orders"]}) == 21


@pytest.mark.asyncio
async def test_execution_quote_uses_separate_iex_feed():
    http = Http(
        [
            (200, [ASSET]),
            (
                200,
                {
                    "symbol": "NVDA",
                    "quote": {
                        "t": "2026-11-10T15:59:59Z",
                        "bp": "200.0100",
                        "ap": "200.0200",
                    },
                },
            ),
        ]
    )
    quote = await provider(http).get_execution_quote("us-equity:NVDA")
    assert parse_qs(urlparse(http.urls[-1]).query)["feed"] == ["iex"]
    assert quote.feed == "iex" and quote.delay_seconds == 0
    assert str(quote.ask_price) == "200.0200"
    assert quote.source == "alpaca_iex_single_exchange"


@pytest.mark.asyncio
async def test_execution_quote_does_not_fall_back_on_feed_error():
    http = Http([(200, [ASSET]), (403, None)])
    with pytest.raises(ProviderAuthorizationError):
        await provider(http).get_execution_quote("us-equity:NVDA")
    assert len(http.urls) == 2


@pytest.mark.asyncio
async def test_execution_quote_never_reuses_a_stale_last_trade():
    http = Http(
        [
            (200, [ASSET]),
            (
                200,
                {
                    "symbol": "NVDA",
                    "quote": {"t": "2026-11-10T15:59:59Z", "bp": 0, "ap": 0},
                    "latestTrade": {"t": "2026-11-09T15:59:59Z", "p": "200"},
                },
            ),
        ]
    )
    quote = await provider(http).get_execution_quote("us-equity:NVDA")
    assert (
        quote.last_price is None and quote.bid_price is None and quote.ask_price is None
    )
