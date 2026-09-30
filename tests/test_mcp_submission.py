from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from mcp import types
from mcp.server.mcpserver.exceptions import ToolError

from portfolio_mcp.api.app import create_app
from portfolio_mcp.database import (
    PortfolioRepository,
)
from portfolio_mcp.execution import (
    ExecutionCommand,
    ExecutionIndeterminateError,
    FixtureExecutionProvider,
)
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventType,
)
from portfolio_mcp.refresh import PortfolioRefreshService
from portfolio_mcp.server import create_server
from portfolio_mcp.trading_safety import (
    TradingGuard,
    TradingSettingsService,
)
from portfolio_mcp.trading_service import (
    McpAuthorizationService,
    OrderDraftService,
    OrderSubmissionService,
    fixture_submission_validator,
)


@pytest.fixture
def mock_clock() -> datetime:
    return datetime(2026, 9, 12, 20, 0, tzinfo=UTC)


async def _setup_services(tmp_path: Any, now: datetime):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    repo = PortfolioRepository(db_url, clock=lambda: now)
    provider = FixturePortfolioProvider()
    await PortfolioRefreshService(provider, repo, clock=lambda: now).refresh()

    repo.replace_trading_settings(
        live_trading_enabled=True,
        kill_switch_active=False,
        max_order_shares=Decimal("100"),
        max_order_notional_usd=Decimal("50000"),
        updated_at=now,
        expected_version=0,
    )

    market_data = FixtureMarketDataProvider()
    settings_service = TradingSettingsService(repo, lambda: now)
    guard = TradingGuard(repo, settings_service)
    draft_service = OrderDraftService(repo, market_data, lambda: now, guard)
    mcp_auth_service = McpAuthorizationService(repo, clock=lambda: now, scrypt_n=1024)
    execution_provider = FixtureExecutionProvider(clock=lambda: now)
    validator = fixture_submission_validator(provider)
    submission_service = OrderSubmissionService(
        repository=repo,
        execution_provider=execution_provider,
        clock=lambda: now,
        validator=validator,
        trading_guard=guard,
        mcp_auth_service=mcp_auth_service,
    )

    return (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    )


@pytest.mark.asyncio
async def test_dashboard_issue_mcp_authorization(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    app = create_app(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        clock=lambda: now,
        draft_service=draft_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
    )

    # 1. Create draft
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Require confirmation: confirmed=False fails
        resp_unconfirmed = await client.post(
            f"/api/order-drafts/{draft.id}/mcp-authorization",
            json={"expected_fingerprint": draft.fingerprint, "confirmed": False},
        )
        assert resp_unconfirmed.status_code in {400, 422}

        # Fingerprint mismatch fails
        resp_mismatch = await client.post(
            f"/api/order-drafts/{draft.id}/mcp-authorization",
            json={"expected_fingerprint": "wrong-fingerprint", "confirmed": True},
        )
        assert resp_mismatch.status_code == 409

        # Success issuance
        resp = await client.post(
            f"/api/order-drafts/{draft.id}/mcp-authorization",
            json={"expected_fingerprint": draft.fingerprint, "confirmed": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_id" in data
        assert "code" in data
        assert len(data["code"]) == 8
        assert data["code"].isdigit()
        assert "expires_at" in data
        assert data["draft"]["id"] == draft.id
        assert data["draft"]["symbol"] == "VTI"
        assert data["draft"]["quantity"] == "5"

        # Verify audit event: AUTHORIZATION_CREATED
        events = repo.list_order_events(draft_id=draft.id)
        created_events = [
            e
            for e in events.items
            if e.event_type == OrderEventType.AUTHORIZATION_CREATED
        ]
        assert len(created_events) == 1
        assert created_events[0].actor == OrderEventActor.DASHBOARD
        assert created_events[0].details["authorization_id"] == data["authorization_id"]
        assert created_events[0].details["action"] == "submit"


@pytest.mark.asyncio
async def test_mcp_submit_authorized_order_success(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    # 1. Create draft
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )

    # 2. Issue code
    created_auth = mcp_auth_service.create_authorization(
        action="submit", target_draft_id=draft.id
    )
    code = created_auth.plaintext_code

    # 3. Call submit_authorized_order via MCP
    result = await server.call_tool(
        "submit_authorized_order",
        {"draft_id": draft.id, "code": code},
    )
    assert isinstance(result, types.CallToolResult)
    assert result.is_error is False
    order_data = result.structured_content
    assert isinstance(order_data, dict)
    order_dict = order_data["order"]
    assert order_dict["state"] in {"ACCEPTED", "FILLED"}
    assert order_dict["instrument"]["symbol"] == "VTI"
    assert order_dict["instruction"]["quantity"] == "5"

    # 4. Verify code is marked consumed
    active = repo.active_mcp_authorization(action="submit", target_draft_id=draft.id)
    assert active is None

    # 5. Verify audit events
    events = repo.list_order_events(draft_id=draft.id)
    event_types = [e.event_type for e in events.items]
    assert OrderEventType.DRAFT_CREATED in event_types
    assert OrderEventType.AUTHORIZATION_CREATED in event_types
    assert OrderEventType.AUTHORIZATION_CONSUMED in event_types
    assert OrderEventType.SUBMISSION_STARTED in event_types


@pytest.mark.asyncio
async def test_mcp_submit_authorized_order_wrong_or_expired_code(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )
    mcp_auth_service.create_authorization(action="submit", target_draft_id=draft.id)

    # 1. Wrong code fails
    with pytest.raises(ToolError, match="invalid_or_expired_code"):
        await server.call_tool(
            "submit_authorized_order",
            {"draft_id": draft.id, "code": "99999999"},
        )

    # Verify provider was NOT called and no order created
    orders = repo.list_orders(account_id="schwab-taxable-demo")
    assert len(orders.items) == 0

    # Verify AUTHORIZATION_FAILED event
    events = repo.list_order_events(draft_id=draft.id)
    failed_events = [
        e for e in events.items if e.event_type == OrderEventType.AUTHORIZATION_FAILED
    ]
    assert len(failed_events) >= 1
    assert failed_events[0].actor == OrderEventActor.MCP


@pytest.mark.asyncio
async def test_mcp_submit_authorized_order_replay_prevented(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )
    auth = mcp_auth_service.create_authorization(
        action="submit", target_draft_id=draft.id
    )

    # First call succeeds
    result1 = await server.call_tool(
        "submit_authorized_order",
        {"draft_id": draft.id, "code": auth.plaintext_code},
    )
    assert isinstance(result1, types.CallToolResult)
    assert result1.is_error is False

    # Second call (replay) fails immediately with invalid_or_expired_code
    # or draft_already_submitted
    with pytest.raises(
        ToolError, match="(invalid_or_expired_code|draft_already_submitted)"
    ):
        await server.call_tool(
            "submit_authorized_order",
            {"draft_id": draft.id, "code": auth.plaintext_code},
        )


@pytest.mark.asyncio
async def test_mcp_submit_authorized_order_concurrency(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )
    auth = mcp_auth_service.create_authorization(
        action="submit", target_draft_id=draft.id
    )
    code = auth.plaintext_code

    success_count = 0
    fail_count = 0

    async def attempt():
        nonlocal success_count, fail_count
        try:
            res = await server.call_tool(
                "submit_authorized_order",
                {"draft_id": draft.id, "code": code},
            )
            if isinstance(res, types.CallToolResult) and not res.is_error:
                success_count += 1
            else:
                fail_count += 1
        except ToolError:
            fail_count += 1

    # Launch 10 concurrent requests
    await asyncio.gather(*(attempt() for _ in range(10)))

    assert success_count == 1
    assert fail_count == 9
    # Exactly one order in the repository
    orders = repo.list_orders(account_id="schwab-taxable-demo")
    assert len(orders.items) == 1


@pytest.mark.asyncio
async def test_mcp_submit_authorized_order_guard_blocks_changed_safety_state(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    # 1. Create draft and issue code while system is safe
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )
    auth = mcp_auth_service.create_authorization(
        action="submit", target_draft_id=draft.id
    )
    code = auth.plaintext_code

    # 2. Safety state changes: Kill switch is engaged!
    repo.replace_trading_settings(
        live_trading_enabled=True,
        kill_switch_active=True,
        max_order_shares=Decimal("100"),
        max_order_notional_usd=Decimal("50000"),
        updated_at=now,
        expected_version=1,
    )

    # 3. MCP submission attempt
    result = await server.call_tool(
        "submit_authorized_order",
        {"draft_id": draft.id, "code": code},
    )
    assert isinstance(result, types.CallToolResult)
    # The order was rejected locally by TradingGuard
    order_data = result.structured_content
    assert isinstance(order_data, dict)
    assert order_data["order"]["state"] == "REJECTED"
    assert order_data["order"]["result"]["code"] == "kill_switch_active"

    # 4. Code was consumed
    active = repo.active_mcp_authorization(action="submit", target_draft_id=draft.id)
    assert active is None

    # 5. Replay fails immediately with invalid_or_expired_code
    # or draft_already_submitted
    with pytest.raises(
        ToolError, match="(invalid_or_expired_code|draft_already_submitted)"
    ):
        await server.call_tool(
            "submit_authorized_order",
            {"draft_id": draft.id, "code": code},
        )


@pytest.mark.asyncio
async def test_mcp_submit_authorized_order_indeterminate_fails_closed(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    # Mock provider submit_order to raise ExecutionIndeterminateError
    async def failing_submit(command: ExecutionCommand):
        raise ExecutionIndeterminateError("Network timeout during order submit")

    execution_provider.submit_order = failing_submit

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )
    auth = mcp_auth_service.create_authorization(
        action="submit", target_draft_id=draft.id
    )

    result = await server.call_tool(
        "submit_authorized_order",
        {"draft_id": draft.id, "code": auth.plaintext_code},
    )
    assert isinstance(result, types.CallToolResult)
    order_data = result.structured_content
    assert isinstance(order_data, dict)
    assert order_data["order"]["state"] == "UNKNOWN"

    # Code is consumed, retry is impossible
    with pytest.raises(
        ToolError, match="(invalid_or_expired_code|draft_already_submitted)"
    ):
        await server.call_tool(
            "submit_authorized_order",
            {"draft_id": draft.id, "code": auth.plaintext_code},
        )


@pytest.mark.asyncio
async def test_mcp_cannot_create_or_retrieve_codes() -> None:
    provider = FixturePortfolioProvider()
    server = create_server(provider)
    tool_names = [tool.name for tool in await server.list_tools()]

    # submit_authorized_order should exist
    assert "submit_authorized_order" in tool_names

    # Verification: MCP cannot create or retrieve codes
    for name in tool_names:
        assert "create_code" not in name
        assert "create_auth" not in name
        assert "get_code" not in name
        assert "issue_code" not in name
        assert "list_codes" not in name


@pytest.mark.asyncio
async def test_submit_authorized_order_input_validation() -> None:
    provider = FixturePortfolioProvider()
    server = create_server(provider)

    with pytest.raises(ToolError, match="invalid_draft_id"):
        await server.call_tool(
            "submit_authorized_order", {"draft_id": "", "code": "12345678"}
        )

    with pytest.raises(ToolError, match="invalid_draft_id"):
        await server.call_tool(
            "submit_authorized_order", {"draft_id": "   ", "code": "12345678"}
        )

    with pytest.raises(ToolError, match="invalid_code"):
        await server.call_tool(
            "submit_authorized_order", {"draft_id": "draft-1", "code": ""}
        )

    with pytest.raises(ToolError, match="invalid_code"):
        await server.call_tool(
            "submit_authorized_order", {"draft_id": "draft-1", "code": "   "}
        )


@pytest.mark.asyncio
async def test_dashboard_issue_mcp_authorization_boundary_errors(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    app = create_app(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        clock=lambda: now,
        draft_service=draft_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
    )

    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Non-existent draft returns 404
        resp_404 = await client.post(
            "/api/order-drafts/non-existent-id/mcp-authorization",
            json={"expected_fingerprint": "abc", "confirmed": True},
        )
        assert resp_404.status_code == 404

        # 2. Expired draft returns 409
        expired_app = create_app(
            provider=provider,
            database_url=db_url,
            market_data_provider=market_data,
            clock=lambda: now + timedelta(minutes=10),
            draft_service=draft_service,
            submission_service=submission_service,
            mcp_auth_service=mcp_auth_service,
        )
        async with AsyncClient(
            transport=ASGITransport(app=expired_app), base_url="http://test"
        ) as expired_client:
            resp_expired = await expired_client.post(
                f"/api/order-drafts/{draft.id}/mcp-authorization",
                json={"expected_fingerprint": draft.fingerprint, "confirmed": True},
            )
            assert resp_expired.status_code == 409
            assert "expired" in resp_expired.json()["detail"].lower()

        # 3. Already submitted draft returns 409
        # First submit the draft via dashboard confirm
        confirm_resp = await client.post(
            f"/api/order-drafts/{draft.id}/confirm",
            json={"expected_fingerprint": draft.fingerprint, "confirmed": True},
        )
        assert confirm_resp.status_code == 200

        # Attempt to issue code for already submitted draft
        resp_conflict = await client.post(
            f"/api/order-drafts/{draft.id}/mcp-authorization",
            json={"expected_fingerprint": draft.fingerprint, "confirmed": True},
        )
        assert resp_conflict.status_code == 409
        assert "already been created" in resp_conflict.json()["detail"]
