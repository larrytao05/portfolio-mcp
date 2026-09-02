from contextlib import AsyncExitStack
from datetime import date
from decimal import Decimal

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.provider import AccountNotFoundError

SERVER_PATH = "./main.py"
EXPECTED_TOOLS = ["list_accounts", "get_holdings", "get_transactions"]


@pytest.mark.asyncio
async def test_mcp_server_connection() -> None:
    async with AsyncExitStack() as exit_stack:
        server_params = StdioServerParameters(
            command="python", args=[SERVER_PATH], env=None
        )
        stdio, write = await exit_stack.enter_async_context(stdio_client(server_params))
        session = await exit_stack.enter_async_context(ClientSession(stdio, write))

        await session.initialize()
        response = await session.list_tools()

    assert sorted(EXPECTED_TOOLS) == sorted(tool.name for tool in response.tools)


@pytest.mark.asyncio
async def test_mcp_tools_return_fixture_data() -> None:
    async with AsyncExitStack() as exit_stack:
        server_params = StdioServerParameters(
            command="python", args=[SERVER_PATH], env=None
        )
        stdio, write = await exit_stack.enter_async_context(stdio_client(server_params))
        session = await exit_stack.enter_async_context(ClientSession(stdio, write))

        await session.initialize()
        response = await session.call_tool(
            "get_holdings", {"account_id": "schwab-taxable-demo"}
        )

    assert isinstance(response, types.CallToolResult)
    assert response.is_error is False
    assert isinstance(response.structured_content, dict)
    assert response.structured_content["account"]["provider"] == "Schwab"
    assert response.structured_content["positions"][0]["symbol"] == "VTI"


@pytest.mark.asyncio
async def test_fixture_accounts_are_safe_and_supported() -> None:
    provider = FixturePortfolioProvider()

    accounts = await provider.list_accounts()

    assert [account.account_type for account in accounts] == [
        "taxable_brokerage",
        "roth_ira",
    ]
    assert [account.provider for account in accounts] == ["Schwab", "Fidelity"]
    assert all("••••" in account.label for account in accounts)


@pytest.mark.asyncio
async def test_fixture_portfolio_is_approximately_ten_thousand_usd() -> None:
    provider = FixturePortfolioProvider()
    accounts = await provider.list_accounts()

    positions = [
        position
        for account in accounts
        for position in (await provider.get_holdings(account.id)).positions
    ]

    assert all(position.market_value is not None for position in positions)
    total_value = sum(
        (position.market_value or Decimal("0") for position in positions), Decimal("0")
    )
    vti_value = sum(
        (
            position.market_value or Decimal("0")
            for position in positions
            if position.symbol == "VTI"
        ),
        Decimal("0"),
    )
    individual_equity_value = sum(
        (
            position.market_value or Decimal("0")
            for position in positions
            if position.symbol in {"SPGI", "NVDA", "MU"}
        ),
        Decimal("0"),
    )

    assert total_value == Decimal("9999.95")
    assert Decimal("0.499") < vti_value / total_value < Decimal("0.501")
    assert Decimal("0.099") < individual_equity_value / total_value < Decimal("0.101")


@pytest.mark.asyncio
async def test_transactions_filter_to_inclusive_date_range() -> None:
    provider = FixturePortfolioProvider()

    history = await provider.get_transactions(
        "schwab-taxable-demo", date(2026, 8, 1), date(2026, 8, 14)
    )

    assert [transaction.id for transaction in history.transactions] == [
        "schwab-demo-003",
        "schwab-demo-002",
    ]


@pytest.mark.asyncio
async def test_unknown_account_is_rejected() -> None:
    provider = FixturePortfolioProvider()

    with pytest.raises(AccountNotFoundError, match="Account not found"):
        await provider.get_holdings("missing-account")
