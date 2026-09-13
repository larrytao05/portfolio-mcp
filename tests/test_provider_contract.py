from datetime import date

import pytest

from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.provider import (
    AccountNotFoundError,
    InstrumentNotFoundError,
    MarketDataProvider,
    PortfolioProvider,
)


@pytest.fixture
def provider() -> PortfolioProvider:
    return FixturePortfolioProvider()


@pytest.fixture
def market_data_provider() -> MarketDataProvider:
    return FixtureMarketDataProvider()


@pytest.mark.asyncio
async def test_provider_contract_returns_masked_supported_accounts(
    provider: PortfolioProvider,
) -> None:
    accounts = await provider.list_accounts()

    assert accounts
    assert all(
        account.id and account.provider and account.currency for account in accounts
    )
    assert all("••••" in account.label for account in accounts)


@pytest.mark.asyncio
async def test_provider_contract_returns_holdings_snapshot_for_an_account(
    provider: PortfolioProvider,
) -> None:
    account = (await provider.list_accounts())[0]

    snapshot = await provider.get_holdings(account.id)

    assert snapshot.account == account
    assert snapshot.as_of >= date(2000, 1, 1)
    assert all(position.account_id == account.id for position in snapshot.positions)


@pytest.mark.asyncio
async def test_provider_contract_returns_reverse_chronological_transactions(
    provider: PortfolioProvider,
) -> None:
    account = (await provider.list_accounts())[0]

    history = await provider.get_transactions(
        account.id, date(2026, 1, 1), date(2026, 12, 31)
    )

    assert history.account == account
    assert all(
        history.start_date <= transaction.occurred_on <= history.end_date
        for transaction in history.transactions
    )
    assert list(history.transactions) == sorted(
        history.transactions,
        key=lambda transaction: transaction.occurred_on,
        reverse=True,
    )


@pytest.mark.asyncio
async def test_provider_contract_rejects_unknown_accounts(
    provider: PortfolioProvider,
) -> None:
    with pytest.raises(AccountNotFoundError, match="Account not found"):
        await provider.get_holdings("missing-account")


@pytest.mark.asyncio
async def test_market_data_contract_searches_canonical_instruments(
    market_data_provider: MarketDataProvider,
) -> None:
    instruments = await market_data_provider.search_instruments("vanguard")

    assert [(instrument.id, instrument.symbol) for instrument in instruments] == [
        ("us-etf:VTI", "VTI"),
    ]
    assert await market_data_provider.search_instruments("not-a-symbol") == []


@pytest.mark.asyncio
async def test_market_data_contract_preserves_unavailable_quote_fields(
    market_data_provider: MarketDataProvider,
) -> None:
    quote = await market_data_provider.get_quote("us-fund:FIXTURE_UNAVAILABLE")

    assert quote.instrument.id == "us-fund:FIXTURE_UNAVAILABLE"
    assert quote.source == "fixture_market_data"
    assert quote.observed_at.isoformat() == "2026-09-12T20:00:00+00:00"
    assert quote.last_price is None
    assert quote.bid_price is None
    assert quote.ask_price is None


@pytest.mark.asyncio
async def test_market_data_contract_rejects_unknown_instruments(
    market_data_provider: MarketDataProvider,
) -> None:
    with pytest.raises(InstrumentNotFoundError, match="Instrument not found"):
        await market_data_provider.get_quote("missing")
