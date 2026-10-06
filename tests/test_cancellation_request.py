from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from mcp import types
from mcp.server.mcpserver.exceptions import ToolError

from portfolio_mcp.api.app import create_app
from portfolio_mcp.execution import (
    OrderState,
)
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.order_cancellation_store import (
    CancellationObservation,
    active_cancellation_request_for_order,
    begin_order_cancellation,
    create_cancellation_request,
    finish_order_cancellation,
    get_cancellation_request,
)
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventType,
)
from portfolio_mcp.order_query_store import list_order_events
from portfolio_mcp.order_query_store import order as load_order
from portfolio_mcp.reconciliation_store import finish_order_reconciliation
from portfolio_mcp.schema import CancellationRequestRecord
from portfolio_mcp.server import create_server
from portfolio_mcp.stored_orders import ConcurrentOrderUpdate
from portfolio_mcp.trading_service import (
    OrderCancellationRequestService,
    OrderCancellationService,
)
from tests.mcp_support import setup_mcp_services


async def _setup_services(tmp_path: Path, now: datetime):
    context = await setup_mcp_services(tmp_path, now)
    cancellation_service = OrderCancellationService(
        database=context.database,
        execution_provider=context.execution,
        clock=lambda: now,
    )
    cancellation_request_service = OrderCancellationRequestService(
        database=context.database,
        clock=lambda: now,
    )
    return (
        *context[:-1],
        cancellation_service,
        cancellation_request_service,
        context.database_url,
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

    cancel_mock = AsyncMock()
    execution_provider.cancel_order = cancel_mock

    server = create_server(
        provider,
        database_url=db_url,
        database=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

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
        database=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    with pytest.raises(ToolError, match="order_not_found"):
        await server.call_tool(
            "request_order_cancellation",
            {"order_id": "non-existent-order"},
        )

    order_id = await _create_active_order(draft_service, submission_service)
    with repo.sessions() as session:
        from portfolio_mcp.schema import OrderRecord

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
        resp_unconfirmed = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": req.action_fingerprint, "confirmed": False},
        )
        assert resp_unconfirmed.status_code in {400, 422}

        resp_mismatch = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": "wrong-fp", "confirmed": True},
        )
        assert resp_mismatch.status_code == 409

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

        events = list_order_events(repo, order_id=order_id)
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
        database=repo,
        draft_service=draft_service,
        submission_service=submission_service,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    with pytest.raises(ToolError, match="invalid_or_expired_code"):
        await server.call_tool(
            "submit_authorized_order",
            {"draft_id": draft.id, "code": cancel_auth.plaintext_code},
        )

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

    with repo.sessions() as session:
        from portfolio_mcp.schema import OrderRecord

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

    expired_req = get_cancellation_request(repo, req.id)
    assert expired_req is not None
    assert expired_req.status == "expired"
    invalidation_events = [
        event
        for event in list_order_events(repo, order_id=order_id).items
        if event.event_type == OrderEventType.CANCELLATION_REQUEST_INVALIDATED
    ]
    assert len(invalidation_events) == 1
    assert invalidation_events[0].details == {
        "request_id": req.id,
        "reason": "expired",
    }


@pytest.mark.asyncio
async def test_mcp_cannot_create_or_retrieve_cancellation_codes() -> None:
    provider = FixturePortfolioProvider()
    server = create_server(provider)
    tool_names = [tool.name for tool in await server.list_tools()]

    assert "request_order_cancellation" in tool_names

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
        resp1 = await client.post(
            f"/api/cancellation-requests/{req.id}/mcp-authorization",
            json={"expected_fingerprint": req.action_fingerprint, "confirmed": True},
        )
        assert resp1.status_code == 200

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
    creation_events = [
        event
        for event in list_order_events(repo, order_id=order_id).items
        if event.event_type == OrderEventType.CANCELLATION_REQUEST_CREATED
    ]
    assert len(creation_events) == 1
    assert creation_events[0].details == {
        "request_id": req.id,
        "expires_at": req.expires_at.isoformat(),
    }

    stored_req, auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )
    assert stored_req.status == "authorized"

    order = load_order(repo, order_id)
    assert order is not None
    finish_order_reconciliation(
        repo,
        order_id,
        attempt_id=uuid4(),
        expected_version=order.version,
        expected_state=order.state,
        state=OrderState.CANCELED,
        now=now + timedelta(seconds=1),
        outcome="canceled",
        result_code="canceled",
        result_message="Canceled by broker",
    )

    reloaded_req = get_cancellation_request(repo, req.id)
    assert reloaded_req is not None
    assert reloaded_req.status == "invalidated"
    assert reloaded_req.invalidation_reason == "order_canceled"
    events = list_order_events(repo, order_id=order_id).items
    invalidation_events = [
        event
        for event in events
        if event.event_type == OrderEventType.CANCELLATION_REQUEST_INVALIDATED
    ]
    assert len(invalidation_events) == 1
    assert invalidation_events[0].details == {
        "request_id": req.id,
        "reason": "order_canceled",
    }

    with pytest.raises(Exception):
        mcp_auth_service.consume_authorization(
            action="cancel",
            target_cancellation_request_id=req.id,
            expected_fingerprint=req.action_fingerprint,
            account_id=req.account_id,
            candidate_code=auth.plaintext_code,
        )


@pytest.mark.asyncio
async def test_stale_order_snapshot_cannot_create_cancellation_request(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    (
        repo,
        _provider,
        _market_data,
        _draft_service,
        _mcp_auth_service,
        _execution_provider,
        submission_service,
        _cancellation_service,
        cancellation_request_service,
        _db_url,
    ) = await _setup_services(tmp_path, now)

    order_id = await _create_active_order(_draft_service, submission_service)
    stale_order = load_order(repo, order_id)
    assert stale_order is not None
    finish_order_reconciliation(
        repo,
        order_id,
        attempt_id=uuid4(),
        expected_version=stale_order.version,
        expected_state=stale_order.state,
        state=stale_order.state,
        now=now + timedelta(seconds=1),
        outcome="matched",
        result_code="matched",
        result_message="Order status confirmed",
    )

    with pytest.raises(ConcurrentOrderUpdate):
        create_cancellation_request(
            repo,
            order=stale_order,
            now=now + timedelta(seconds=2),
            expires_at=now + timedelta(minutes=5),
        )

    assert active_cancellation_request_for_order(repo, order_id) is None
    events = list_order_events(repo, order_id=order_id).items
    assert (
        sum(
            event.event_type == OrderEventType.CANCELLATION_REQUEST_CREATED
            for event in events
        )
        == 0
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

    stored_req1, auth1 = mcp_auth_service.authorize_cancellation_request(
        req1.id, expected_fingerprint=req1.action_fingerprint
    )
    assert stored_req1.status == "authorized"

    req2 = cancellation_request_service.create_request(order_id)
    assert req2.id != req1.id

    reloaded_req1 = get_cancellation_request(repo, req1.id)
    assert reloaded_req1 is not None
    assert reloaded_req1.status == "invalidated"
    assert reloaded_req1.invalidation_reason == "superseded"

    with pytest.raises(Exception):
        mcp_auth_service.consume_authorization(
            action="cancel",
            target_cancellation_request_id=req1.id,
            expected_fingerprint=req1.action_fingerprint,
            account_id=req1.account_id,
            candidate_code=auth1.plaintext_code,
        )


@pytest.mark.asyncio
async def test_finish_order_cancellation_invalidates_active_cancellation_requests(
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
    order = load_order(repo, order_id)
    assert order is not None

    req = cancellation_request_service.create_request(order_id)
    assert req.status == "pending"

    stored_order, attempt_id, started = begin_order_cancellation(
        repo,
        order_id,
        expected_version=order.version,
        expected_state=order.state,
        now=now,
    )
    assert started is True
    assert attempt_id is not None

    reloaded_req1 = get_cancellation_request(repo, req.id)
    assert reloaded_req1 is not None
    assert reloaded_req1.status == "invalidated"
    assert reloaded_req1.invalidation_reason == "cancellation_started"

    req2_id = str(uuid4())
    with repo.sessions.begin() as session:
        session.add(
            CancellationRequestRecord(
                id=req2_id,
                order_id=order_id,
                expected_order_version=order.version,
                expected_order_state=order.state.value,
                account_id=order.account_id,
                provider=order.provider,
                symbol=order.symbol,
                broker_order_id=order.broker_order_id,
                remaining_quantity=order.remaining_quantity or "5",
                action_fingerprint="fp-test",
                created_at=now,
                expires_at=now + timedelta(minutes=5),
                status="pending",
                invalidation_reason=None,
            )
        )

    canceled_order = finish_order_cancellation(
        repo,
        order_id,
        attempt_id=attempt_id,
        observation=CancellationObservation(kind="canceled"),
        now=now,
    )
    assert canceled_order.state == OrderState.CANCELED

    reloaded_req2 = get_cancellation_request(repo, req2_id)
    assert reloaded_req2 is not None
    assert reloaded_req2.status == "invalidated"
    assert reloaded_req2.invalidation_reason == "order_canceled"
