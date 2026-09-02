from datetime import date

import pytest

from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.provider import AccountNotFoundError, PortfolioProvider


@pytest.fixture
def provider() -> PortfolioProvider:
    return FixturePortfolioProvider()


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
