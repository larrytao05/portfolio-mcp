from datetime import UTC, datetime

import pytest

from portfolio_mcp.config import SchwabMarketDataSettings
from portfolio_mcp.provider import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from portfolio_mcp.schwab_market_data import SchwabMarketDataProvider


class FakeSchwabHttpClient:
    def __init__(self, responses: list[tuple[int, object] | Exception]) -> None:
        self._responses = iter(responses)
        self.urls: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        self.urls.append(url)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_schwab_market_data_returns_a_normalized_quote() -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient(
            [
                (200, {"access_token": "access-token", "expires_in": 1800}),
                (
                    200,
                    {
                        "AAPL": {
                            "assetMainType": "EQUITY",
                            "quote": {
                                "lastPrice": 210.75,
                                "bidPrice": 210.5,
                                "askPrice": 211.0,
                                "quoteTime": 1_726_316_800_000,
                            },
                            "reference": {
                                "symbol": "AAPL",
                                "description": "APPLE INC",
                                "exchangeName": "NASDAQ",
                            },
                        }
                    },
                ),
            ]
        ),
        clock=lambda: datetime(2024, 9, 15, 12, 0, tzinfo=UTC),
    )

    quote = await provider.get_quote("us-equity:AAPL")

    assert quote.instrument.id == "us-equity:AAPL"
    assert quote.instrument.symbol == "AAPL"
    assert quote.instrument.name == "APPLE INC"
    assert quote.instrument.asset_class == "equity"
    assert quote.instrument.exchange == "NASDAQ"
    assert quote.source == "schwab_market_data"
    assert quote.observed_at == datetime(2024, 9, 14, 12, 26, 40, tzinfo=UTC)
    assert str(quote.last_price) == "210.75"
    assert str(quote.bid_price) == "210.5"
    assert str(quote.ask_price) == "211.0"


@pytest.mark.asyncio
async def test_schwab_market_data_reads_symbol_from_live_quote_record() -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient(
            [
                (200, {"access_token": "access-token"}),
                (
                    200,
                    {
                        "VTI": {
                            "symbol": "VTI",
                            "assetMainType": "EQUITY",
                            "quote": {"lastPrice": 300.0},
                            "reference": {
                                "description": "VANGUARD TOTAL STOCK MARKET ETF",
                                "exchangeName": "NYSE Arca",
                            },
                        }
                    },
                ),
            ]
        ),
    )

    quote = await provider.get_quote("us-etf:VTI")

    assert quote.instrument.symbol == "VTI"
    assert quote.instrument.name == "VANGUARD TOTAL STOCK MARKET ETF"
    assert quote.instrument.exchange == "NYSE Arca"
    assert quote.instrument.asset_class == "etf"


@pytest.mark.asyncio
async def test_schwab_market_data_encodes_special_symbols_in_quote_paths() -> None:
    client = FakeSchwabHttpClient(
        [
            (200, {"access_token": "access-token"}),
            (
                200,
                {
                    "SPY 250117C00500000": {
                        "symbol": "SPY 250117C00500000",
                        "assetMainType": "OPTION",
                        "quote": {"lastPrice": 3.25},
                        "reference": {"description": "SPY call"},
                    }
                },
            ),
        ]
    )
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=client,
    )

    await provider.get_quote("us-option:SPY 250117C00500000")

    assert client.urls[-1].endswith("/SPY%20250117C00500000/quotes")


@pytest.mark.asyncio
async def test_schwab_market_data_searches_normalized_instruments() -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient(
            [
                (200, {"access_token": "access-token"}),
                (
                    200,
                    [
                        {
                            "symbol": "VTI",
                            "description": "VANGUARD TOTAL STOCK MARKET ETF",
                            "assetType": "ETF",
                            "exchange": "ARCX",
                            "currency": "USD",
                        }
                    ],
                ),
            ]
        ),
    )

    instruments = await provider.search_instruments("vanguard")

    assert [instrument.id for instrument in instruments] == ["us-etf:VTI"]
    assert [instrument.symbol for instrument in instruments] == ["VTI"]
    assert instruments[0].name == "VANGUARD TOTAL STOCK MARKET ETF"
    assert instruments[0].asset_class == "etf"
    assert instruments[0].exchange == "ARCX"
    assert instruments[0].currency == "USD"


@pytest.mark.asyncio
async def test_schwab_market_data_searches_live_response_envelope() -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient(
            [
                (200, {"access_token": "access-token"}),
                (
                    200,
                    {
                        "instruments": [
                            {
                                "symbol": "VTI",
                                "description": "VANGUARD TOTAL STOCK MARKET ETF",
                                "assetType": "ETF",
                                "exchange": "ARCX",
                            }
                        ]
                    },
                ),
            ]
        ),
    )

    instruments = await provider.search_instruments("VTI")

    assert [(instrument.id, instrument.symbol) for instrument in instruments] == [
        ("us-etf:VTI", "VTI")
    ]


@pytest.mark.asyncio
async def test_schwab_market_data_sanitizes_authentication_failure() -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient([(401, {"error": "raw broker detail"})]),
    )

    with pytest.raises(
        ProviderAuthenticationError, match="Unable to authenticate with Schwab"
    ):
        await provider.search_instruments("VTI")


@pytest.mark.asyncio
async def test_schwab_market_data_sanitizes_timeout() -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient([TimeoutError()]),
    )

    with pytest.raises(
        ProviderUnavailableError, match="Schwab market data is temporarily unavailable"
    ):
        await provider.search_instruments("VTI")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (403, ProviderAuthorizationError),
        (429, ProviderRateLimitError),
        (503, ProviderUnavailableError),
    ],
)
async def test_schwab_market_data_sanitizes_provider_statuses(
    status: int, error_type: type[Exception]
) -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient([(status, {"error": "raw broker detail"})]),
    )

    with pytest.raises(error_type):
        await provider.search_instruments("VTI")


@pytest.mark.asyncio
async def test_schwab_market_data_sanitizes_malformed_token_response() -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient([(200, {})]),
    )

    with pytest.raises(
        ProviderResponseError,
        match="Schwab market data returned an unexpected response",
    ):
        await provider.search_instruments("VTI")


@pytest.mark.asyncio
async def test_schwab_market_data_sanitizes_invalid_quote_timestamp() -> None:
    provider = SchwabMarketDataProvider(
        SchwabMarketDataSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        ),
        http_client=FakeSchwabHttpClient(
            [
                (200, {"access_token": "access-token"}),
                (
                    200,
                    {
                        "VTI": {
                            "symbol": "VTI",
                            "assetMainType": "EQUITY",
                            "quote": {"quoteTime": 10**300},
                            "reference": {
                                "description": "Vanguard Total Stock Market ETF"
                            },
                        }
                    },
                ),
            ]
        ),
    )

    with pytest.raises(
        ProviderResponseError,
        match="Schwab market data returned an unexpected response",
    ):
        await provider.get_quote("us-etf:VTI")
