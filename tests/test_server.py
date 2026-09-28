from contextlib import AsyncExitStack
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from portfolio_mcp.database import (
    OrderDraftRecord,
    OrderRecord,
    PortfolioRepository,
)
from portfolio_mcp.execution import OrderState
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.order_history import OrderStatusSource
from portfolio_mcp.provider import AccountNotFoundError
from portfolio_mcp.refresh import PortfolioRefreshService
from portfolio_mcp.server import create_server

SERVER_PATH = "./main.py"
EXPECTED_TOOLS = [
    "list_accounts",
    "get_holdings",
    "get_transactions",
    "get_trading_status",
    "search_instruments",
    "create_order_draft",
    "get_order_draft",
    "list_orders",
    "get_order",
    "submit_authorized_order",
]


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
async def test_tool_descriptions_conformance() -> None:
    async with AsyncExitStack() as exit_stack:
        server_params = StdioServerParameters(
            command="python", args=[SERVER_PATH], env=None
        )
        stdio, write = await exit_stack.enter_async_context(stdio_client(server_params))
        session = await exit_stack.enter_async_context(ClientSession(stdio, write))

        await session.initialize()
        response = await session.list_tools()

    tools_by_name = {tool.name: tool for tool in response.tools}

    # Verify read-only claims and draft vs order disclaimers
    read_only_tools = [
        "list_accounts",
        "get_holdings",
        "get_transactions",
        "get_trading_status",
        "search_instruments",
        "get_order_draft",
        "list_orders",
        "get_order",
    ]
    for name in read_only_tools:
        description = (tools_by_name[name].description or "").lower()
        assert "read-only" in description, f"{name} must state it is read-only"

    # Draft tools must state drafts are not orders and require independent review
    for name in ["create_order_draft", "get_order_draft"]:
        description = (tools_by_name[name].description or "").lower()
        assert "not order" in description or "not place" in description, (
            f"{name} must clarify drafts are not orders"
        )
        assert "dashboard" in description or "independent" in description, (
            f"{name} must mention dashboard review/confirmation"
        )


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
async def test_get_trading_status_tool(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'test.db'}", clock=lambda: now)
    server = create_server(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        repository=repo,
        clock=lambda: now,
    )

    result = await server.call_tool("get_trading_status", {})
    assert isinstance(result, types.CallToolResult)
    assert result.is_error is False
    data = result.structured_content
    assert isinstance(data, dict)
    assert "providers" in data
    assert "accounts" in data
    # Ensure sensitive settings and limits are NOT leaked
    assert "settings" not in data
    assert "limits" not in data
    assert "max_order_notional_usd" not in str(data)
    assert "kill_switch_active" not in str(data)


@pytest.mark.asyncio
async def test_search_instruments_tool(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    server = create_server(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        clock=lambda: now,
    )

    result = await server.call_tool("search_instruments", {"query": "VTI"})
    assert isinstance(result, types.CallToolResult)
    assert result.is_error is False
    data = result.structured_content
    assert isinstance(data, dict)
    assert "instruments" in data
    assert any(inst["symbol"] == "VTI" for inst in data["instruments"])


@pytest.mark.asyncio
async def test_create_and_get_order_draft_tools(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    provider = FixturePortfolioProvider()
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'test.db'}", clock=lambda: now)

    # Initialize account state and enable live trading
    await PortfolioRefreshService(provider, repo, clock=lambda: now).refresh()
    repo.replace_trading_settings(
        live_trading_enabled=True,
        kill_switch_active=False,
        max_order_shares=Decimal("100"),
        max_order_notional_usd=Decimal("50000"),
        updated_at=now,
        expected_version=0,
    )

    server = create_server(
        provider,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        repository=repo,
        clock=lambda: now,
    )

    # 1. Create order draft
    create_result = await server.call_tool(
        "create_order_draft",
        {
            "account_id": "schwab-taxable-demo",
            "instrument_id": "us-etf:VTI",
            "side": "buy",
            "order_type": "limit",
            "quantity": "5",
            "limit_price": "220.00",
        },
    )
    assert isinstance(create_result, types.CallToolResult)
    assert create_result.is_error is False
    create_data = create_result.structured_content
    assert isinstance(create_data, dict)
    assert "draft" in create_data
    draft = create_data["draft"]
    draft_id = draft["id"]
    assert draft["instrument"]["symbol"] == "VTI"
    assert draft["instruction"]["quantity"] == "5"
    assert draft["fingerprint"]

    # 2. Verify that creating a draft did NOT create an order
    order_page = repo.list_orders()
    assert len(order_page.items) == 0

    # 3. Get order draft by ID
    get_result = await server.call_tool("get_order_draft", {"draft_id": draft_id})
    assert isinstance(get_result, types.CallToolResult)
    assert get_result.is_error is False
    get_data = get_result.structured_content
    assert isinstance(get_data, dict)
    assert get_data["draft"]["id"] == draft_id
    assert get_data["draft"]["fingerprint"] == draft["fingerprint"]

    # 4. Non-existent draft raises ToolError
    with pytest.raises(ToolError, match="draft_not_found"):
        await server.call_tool("get_order_draft", {"draft_id": "missing-draft"})


@pytest.mark.asyncio
async def test_create_order_draft_guard_rejection(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    provider = FixturePortfolioProvider()
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'test.db'}", clock=lambda: now)

    await PortfolioRefreshService(provider, repo, clock=lambda: now).refresh()
    # Activate kill switch in settings
    repo.replace_trading_settings(
        live_trading_enabled=True,
        kill_switch_active=True,
        max_order_shares=Decimal("100"),
        max_order_notional_usd=Decimal("50000"),
        updated_at=now,
        expected_version=0,
    )

    server = create_server(
        provider,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        repository=repo,
        clock=lambda: now,
    )

    with pytest.raises(ToolError, match="kill_switch_active"):
        await server.call_tool(
            "create_order_draft",
            {
                "account_id": "schwab-taxable-demo",
                "instrument_id": "us-etf:VTI",
                "side": "buy",
                "order_type": "limit",
                "quantity": "5",
                "limit_price": "220.00",
            },
        )


@pytest.mark.asyncio
async def test_list_orders_and_get_order_tools(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    provider = FixturePortfolioProvider()
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'test.db'}", clock=lambda: now)

    await PortfolioRefreshService(provider, repo, clock=lambda: now).refresh()

    # Seed an order in repository
    with repo._sessions.begin() as session:
        session.add(
            OrderDraftRecord(
                id="draft-order-1",
                account_id="schwab-taxable-demo",
                account_label="Schwab Taxable ••••4821",
                provider="Schwab",
                instrument_id="us-etf:VTI",
                symbol="VTI",
                instrument_name="Vanguard Total Stock Market ETF",
                asset_class="equity_etf",
                side="buy",
                order_type="limit",
                quantity=Decimal("10"),
                limit_price=Decimal("220.00"),
                quote_observed_at=now,
                quote_last_price=Decimal("220.00"),
                quote_bid_price=Decimal("219.95"),
                quote_ask_price=Decimal("220.05"),
                quote_source="fixture",
                warnings="[]",
                fingerprint="fp-12345",
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            )
        )
        session.add(
            OrderRecord(
                id="order-test-1",
                draft_id="draft-order-1",
                client_order_id="client-test-1",
                fingerprint="fp-12345",
                account_id="schwab-taxable-demo",
                account_label="Schwab Taxable ••••4821",
                provider="Schwab",
                instrument_id="us-etf:VTI",
                symbol="VTI",
                side="buy",
                order_type="limit",
                quantity=Decimal("10"),
                limit_price=Decimal("220.00"),
                state=OrderState.ACCEPTED.value,
                broker_order_id="broker-12345",
                result_code="accepted",
                result_message="Order accepted by broker",
                result_source=OrderStatusSource.PROVIDER.value,
                filled_quantity=Decimal("0"),
                average_fill_price=None,
                provider_submission_started_at=now,
                provider_updated_at=now,
                provider_status_label="Open",
                created_at=now,
                updated_at=now,
                version=1,
            )
        )

    server = create_server(
        provider,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        repository=repo,
        clock=lambda: now,
    )

    # 1. List orders
    list_result = await server.call_tool(
        "list_orders",
        {"account_id": "schwab-taxable-demo", "limit": 10},
    )
    assert isinstance(list_result, types.CallToolResult)
    assert list_result.is_error is False
    list_data = list_result.structured_content
    assert isinstance(list_data, dict)
    assert "orders" in list_data
    assert len(list_data["orders"]) == 1
    order_item = list_data["orders"][0]
    assert order_item["id"] == "order-test-1"
    assert order_item["instrument"]["symbol"] == "VTI"

    # 2. Get order by ID
    get_result = await server.call_tool("get_order", {"order_id": "order-test-1"})
    assert isinstance(get_result, types.CallToolResult)
    assert get_result.is_error is False
    get_data = get_result.structured_content
    assert isinstance(get_data, dict)
    assert get_data["order"]["id"] == "order-test-1"

    # 3. Missing order raises ToolError
    with pytest.raises(ToolError, match="order_not_found"):
        await server.call_tool("get_order", {"order_id": "missing-order"})


@pytest.mark.asyncio
async def test_stdio_error_returns_is_error() -> None:
    async with AsyncExitStack() as exit_stack:
        server_params = StdioServerParameters(
            command="python", args=[SERVER_PATH], env=None
        )
        stdio, write = await exit_stack.enter_async_context(stdio_client(server_params))
        session = await exit_stack.enter_async_context(ClientSession(stdio, write))

        await session.initialize()
        # Call with non-existent draft ID
        response = await session.call_tool(
            "get_order_draft", {"draft_id": "missing-draft-123"}
        )

    assert isinstance(response, types.CallToolResult)
    assert response.is_error is True
    assert any(
        isinstance(block, types.TextContent) and "draft_not_found" in block.text
        for block in response.content
    )


@pytest.mark.asyncio
async def test_mcp_security_boundary_no_forbidden_tools() -> None:
    async with AsyncExitStack() as exit_stack:
        server_params = StdioServerParameters(
            command="python", args=[SERVER_PATH], env=None
        )
        stdio, write = await exit_stack.enter_async_context(stdio_client(server_params))
        session = await exit_stack.enter_async_context(ClientSession(stdio, write))

        await session.initialize()
        response = await session.list_tools()

    tool_names = [tool.name for tool in response.tools]
    assert "submit_authorized_order" in tool_names

    forbidden_tool_patterns = [
        "settings",
        "limit",
        "kill_switch",
        "create_code",
        "issue_code",
        "get_code",
        "list_codes",
        "create_auth",
        "cancel",
    ]
    for name in tool_names:
        for pattern in forbidden_tool_patterns:
            assert pattern not in name.lower(), (
                f"Tool '{name}' violates security boundary by including '{pattern}'"
            )


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
