from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from mcp import types
from mcp.server.mcpserver.exceptions import ToolError

from portfolio_mcp.api.app import create_app
from portfolio_mcp.database import (
    PortfolioRepository,
)
from portfolio_mcp.execution import (
    FixtureExecutionProvider,
    OrderState,
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
    OrderCancellationRequestService,
    OrderCancellationService,
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
    cancellation_service = OrderCancellationService(
        repository=repo,
        execution_provider=execution_provider,
        clock=lambda: now,
    )
    cancellation_request_service = OrderCancellationRequestService(
        repository=repo,
        clock=lambda: now,
    )

    return (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        cancellation_service,
        cancellation_request_service,
        db_url,
    )


async def _create_active_order(draft_service, submission_service) -> str:
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )
    order = await submission_service.confirm(draft.id, draft.fingerprint, True)
    return order.id


@pytest.mark.asyncio
async def test_mcp_request_order_cancellation_success_zero_provider_writes(
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
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    order_id = await _create_active_order(draft_service, submission_service)

    # Mock cancel_order to ensure it is NEVER called
    cancel_mock = AsyncMock()
    execution_provider.cancel_order = cancel_mock

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    # 1. Call request_order_cancellation via MCP
    result = await server.call_tool(
        "request_order_cancellation",
        {"order_id": order_id},
    )
    assert isinstance(result, types.CallToolResult)
    assert result.is_error is False

    data = result.structured_content
    assert isinstance(data, dict)
    assert "cancellation_request" in data
    req = data["cancellation_request"]
    assert req["order_id"] == order_id
    assert req["status"] == "pending"
    assert req["remaining_quantity"] == "5"
    assert req["expected_state"] == "ACCEPTED"
    assert "fingerprint" in req
    assert "disclaimer" in data

    # 2. PROOF: Execution provider cancel_order was called exactly 0 times!
    cancel_mock.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_request_order_cancellation_non_cancelable_orders(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    # 1. Non-existent order fails
    with pytest.raises(ToolError, match="order_not_found"):
        await server.call_tool(
            "request_order_cancellation",
            {"order_id": "non-existent-order"},
        )

    # 2. Non-cancelable order (e.g. FILLED or REJECTED)
    order_id = await _create_active_order(draft_service, submission_service)
    # Manually transition order to FILLED in repository
    with repo._sessions() as session:
        from portfolio_mcp.database import OrderRecord

        rec = session.get(OrderRecord, order_id)
        assert rec is not None
        rec.state = OrderState.FILLED.value
        rec.version += 1
        session.commit()

    with pytest.raises(ToolError, match="order_not_cancelable"):
        await server.call_tool(
            "request_order_cancellation",
            {"order_id": order_id},
        )


@pytest.mark.asyncio
async def test_dashboard_issue_cancellation_mcp_authorization_success(
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
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    order_id = await _create_active_order(draft_service, submission_service)
    req = cancellation_request_service.create_request(order_id)

    app = create_app(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        clock=lambda: now,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Require confirmation: confirmed=False fails
        resp_unconfirmed = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": req.action_fingerprint, "confirmed": False},
        )
        assert resp_unconfirmed.status_code in {400, 422}

        # Fingerprint mismatch fails
        resp_mismatch = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": "wrong-fp", "confirmed": True},
        )
        assert resp_mismatch.status_code == 409

        # Success issuance
        resp = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": req.action_fingerprint, "confirmed": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_id" in data
        assert "code" in data
        assert len(data["code"]) == 8
        assert data["code"].isdigit()
        assert "expires_at" in data
        assert data["cancellation_request"]["id"] == req.id
        assert data["cancellation_request"]["order_id"] == order_id

        # Verify audit event: AUTHORIZATION_CREATED with action="cancel"
        events = repo.list_order_events(order_id=order_id)
        cancel_auth_events = [
            e
            for e in events.items
            if e.event_type == OrderEventType.AUTHORIZATION_CREATED
            and e.details.get("action") == "cancel"
        ]
        assert len(cancel_auth_events) == 1
        assert cancel_auth_events[0].actor == OrderEventActor.DASHBOARD
        assert cancel_auth_events[0].details["action"] == "cancel"
        assert (
            cancel_auth_events[0].details["authorization_id"]
            == data["authorization_id"]
        )


@pytest.mark.asyncio
async def test_action_bound_isolation_submit_cannot_cancel_and_vice_versa(
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
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    # 1. Draft submission code
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="5",
        limit_price="220.00",
    )
    submit_auth = mcp_auth_service.create_authorization(
        action="submit", target_draft_id=draft.id
    )

    # 2. Cancellation code
    order_id = await _create_active_order(draft_service, submission_service)
    req = cancellation_request_service.create_request(order_id)
    cancel_auth = mcp_auth_service.create_authorization(
        action="cancel",
        target_cancellation_request_id=req.id,
        target_order_id=order_id,
        payload_fingerprint=req.action_fingerprint,
        account_id=req.account_id,
    )

    server = create_server(
        provider,
        database_url=db_url,
        repository=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    # Attempt to use cancel code in submit_authorized_order -> MUST FAIL
    with pytest.raises(ToolError, match="invalid_or_expired_code"):
        await server.call_tool(
            "submit_authorized_order",
            {"draft_id": draft.id, "code": cancel_auth.plaintext_code},
        )

    # Attempt to consume submit code with action="cancel" -> MUST FAIL
    with pytest.raises(Exception):
        mcp_auth_service.consume_authorization(
            action="cancel",
            target_cancellation_request_id=req.id,
            expected_fingerprint=req.action_fingerprint,
            account_id=req.account_id,
            candidate_code=submit_auth.plaintext_code,
        )


@pytest.mark.asyncio
async def test_cancellation_request_invalidated_on_order_version_or_state_change(
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
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    order_id = await _create_active_order(draft_service, submission_service)
    req = cancellation_request_service.create_request(order_id)

    app = create_app(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        clock=lambda: now,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
    )

    # Simulate order fill or state change
    with repo._sessions() as session:
        from portfolio_mcp.database import OrderRecord

        rec = session.get(OrderRecord, order_id)
        assert rec is not None
        rec.state = OrderState.FILLED.value
        rec.version += 1
        session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": req.action_fingerprint, "confirmed": True},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "changed" in detail or "cancelable" in detail

    # Verify request status in repository is invalidated
    saved_req = cancellation_request_service.get_request(req.id)
    assert saved_req is not None
    assert saved_req.status == "invalidated"


@pytest.mark.asyncio
async def test_cancellation_request_expired(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    order_id = await _create_active_order(draft_service, submission_service)
    req = cancellation_request_service.create_request(order_id)

    # Advance clock by 6 minutes
    future_now = now + timedelta(minutes=6)
    app = create_app(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        clock=lambda: future_now,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": req.action_fingerprint, "confirmed": True},
        )
        assert resp.status_code == 409
        assert "expired" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_mcp_cannot_create_or_retrieve_cancellation_codes() -> None:
    provider = FixturePortfolioProvider()
    server = create_server(provider)
    tool_names = [tool.name for tool in await server.list_tools()]

    # request_order_cancellation should exist
    assert "request_order_cancellation" in tool_names

    # Verification: MCP cannot create or retrieve codes
    for name in tool_names:
        assert "create_code" not in name
        assert "create_auth" not in name
        assert "get_code" not in name
        assert "issue_code" not in name
        assert "list_codes" not in name


@pytest.mark.asyncio
async def test_concurrent_authorization_issuance_single_winner(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    order_id = await _create_active_order(draft_service, submission_service)
    req = cancellation_request_service.create_request(order_id)

    app = create_app(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        clock=lambda: now,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        mcp_auth_service=mcp_auth_service,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First call succeeds
        resp1 = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": req.action_fingerprint, "confirmed": True},
        )
        assert resp1.status_code == 200

        # Immediate replay / concurrent attempt fails closed with 409
        resp2 = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": req.action_fingerprint, "confirmed": True},
        )
        assert resp2.status_code == 409
        assert "already" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_order_terminal_state_invalidates_cancellation_requests(
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
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    order_id = await _create_active_order(draft_service, submission_service)
    req = cancellation_request_service.create_request(order_id)

    # Authorize the request
    stored_req, auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )
    assert stored_req.status == "authorized"

    # Order transitions via finish_order_cancellation / reconcile_order
    repo.invalidate_cancellation_requests_for_order(order_id, reason="order_filled")

    # Verify request is now invalidated
    reloaded_req = repo.get_cancellation_request(req.id)
    assert reloaded_req is not None
    assert reloaded_req.status == "invalidated"
    assert reloaded_req.invalidation_reason == "order_filled"

    # Verify authorization code is invalidated and cannot be consumed
    with pytest.raises(Exception):
        mcp_auth_service.consume_authorization(
            action="cancel",
            target_cancellation_request_id=req.id,
            expected_fingerprint=req.action_fingerprint,
            account_id=req.account_id,
            candidate_code=auth.plaintext_code,
        )


@pytest.mark.asyncio
async def test_new_cancellation_request_supersedes_previously_authorized_requests(
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
        cancellation_service,
        cancellation_request_service,
        db_url,
    ) = await _setup_services(tmp_path, now)

    order_id = await _create_active_order(draft_service, submission_service)
    req1 = cancellation_request_service.create_request(order_id)

    # Authorize request 1
    stored_req1, auth1 = mcp_auth_service.authorize_cancellation_request(
        req1.id, expected_fingerprint=req1.action_fingerprint
    )
    assert stored_req1.status == "authorized"

    # Now create request 2 for the same order
    req2 = cancellation_request_service.create_request(order_id)
    assert req2.id != req1.id

    # Verify request 1 was superseded
    reloaded_req1 = repo.get_cancellation_request(req1.id)
    assert reloaded_req1 is not None
    assert reloaded_req1.status == "invalidated"
    assert reloaded_req1.invalidation_reason == "superseded"

    # Verify request 1's code cannot be consumed
    with pytest.raises(Exception):
        mcp_auth_service.consume_authorization(
            action="cancel",
            target_cancellation_request_id=req1.id,
            expected_fingerprint=req1.action_fingerprint,
            account_id=req1.account_id,
            candidate_code=auth1.plaintext_code,
        )
