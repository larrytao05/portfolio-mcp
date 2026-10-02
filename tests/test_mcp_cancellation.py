from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import types
from mcp.server.mcpserver.exceptions import ToolError

from portfolio_mcp.execution import (
    ExecutionIndeterminateError,
    ExecutionResult,
    OrderState,
)
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventType,
)
from portfolio_mcp.server import create_server
from portfolio_mcp.trading_service import (
    OrderCancellationRequestService,
    OrderCancellationService,
)
from tests.mcp_support import setup_mcp_services


async def _setup_services(tmp_path: Path, now: datetime):
    context = await setup_mcp_services(tmp_path, now)
    cancellation_service = OrderCancellationService(
        repository=context.repository,
        execution_provider=context.execution,
        clock=lambda: now,
        mcp_auth_service=context.authorization,
    )
    cancellation_request_service = OrderCancellationRequestService(
        repository=context.repository,
        clock=lambda: now,
        execution_provider=context.execution,
        mcp_auth_service=context.authorization,
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
async def test_mcp_cancel_authorized_order_success(tmp_path) -> None:
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

    server = create_server(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        execution_provider=execution_provider,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    req_res = await server.call_tool(
        "create_order_cancellation", {"order_id": order_id}
    )
    data = req_res.model_dump()
    assert "cancellation_request" in data["content"][0]["text"]
    active_req = repo.active_cancellation_request_for_order(order_id)
    assert active_req is not None
    req = cancellation_request_service.get_request(active_req.id)
    assert req is not None
    assert req.status == "pending"

    stored_req, created_auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )
    assert created_auth.plaintext_code is not None

    res = await server.call_tool(
        "cancel_authorized_order",
        {
            "cancellation_request_id": req.id,
            "code": created_auth.plaintext_code,
        },
    )
    assert isinstance(res, types.CallToolResult)
    assert not res.is_error
    order_data = res.model_dump()
    text = order_data["content"][0]["text"]
    assert "canceled" in text.lower()

    updated_order = repo.order(order_id)
    assert updated_order is not None
    assert updated_order.state == OrderState.CANCELED

    events = repo.list_order_events(order_id=order_id, limit=50).items
    consumed_events = [
        e
        for e in events
        if e.event_type == OrderEventType.AUTHORIZATION_CONSUMED
        and e.details.get("action") == "cancel"
    ]
    assert len(consumed_events) == 1
    assert consumed_events[0].actor == OrderEventActor.MCP

    saved_req = cancellation_request_service.get_request(req.id)
    assert saved_req is not None
    assert saved_req.status == "attempted"


@pytest.mark.asyncio
async def test_mcp_cancel_authorized_order_wrong_or_expired_code(tmp_path) -> None:
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

    server = create_server(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        execution_provider=execution_provider,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    req = cancellation_request_service.create_request(order_id)
    stored_req, created_auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )

    with pytest.raises(ToolError, match="invalid_or_expired_code"):
        await server.call_tool(
            "cancel_authorized_order",
            {"cancellation_request_id": req.id, "code": "WRONGCOD"},
        )

    order = repo.order(order_id)
    assert order is not None
    assert order.state == OrderState.ACCEPTED


@pytest.mark.asyncio
async def test_mcp_cancel_authorized_order_replay_prevented(tmp_path) -> None:
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

    server = create_server(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        execution_provider=execution_provider,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    req = cancellation_request_service.create_request(order_id)
    _, created_auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )

    await server.call_tool(
        "cancel_authorized_order",
        {
            "cancellation_request_id": req.id,
            "code": created_auth.plaintext_code,
        },
    )

    with pytest.raises(
        ToolError,
        match="(invalid_or_expired_code|cancellation_request_already_consumed)",
    ):
        await server.call_tool(
            "cancel_authorized_order",
            {
                "cancellation_request_id": req.id,
                "code": created_auth.plaintext_code,
            },
        )


@pytest.mark.asyncio
async def test_mcp_cancel_authorized_order_submit_code_rejected(tmp_path) -> None:
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

    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="220.00",
    )
    submit_auth = mcp_auth_service.create_authorization(
        action="submit", target_draft_id=draft.id
    )

    req = cancellation_request_service.create_request(order_id)
    mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )

    server = create_server(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        execution_provider=execution_provider,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    with pytest.raises(ToolError, match="invalid_or_expired_code"):
        await server.call_tool(
            "cancel_authorized_order",
            {
                "cancellation_request_id": req.id,
                "code": submit_auth.plaintext_code,
            },
        )


@pytest.mark.asyncio
async def test_mcp_cancel_authorized_order_concurrent_single_winner(tmp_path) -> None:
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

    server = create_server(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        execution_provider=execution_provider,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    req = cancellation_request_service.create_request(order_id)
    _, created_auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )

    async def call_cancel():
        try:
            return await server.call_tool(
                "cancel_authorized_order",
                {
                    "cancellation_request_id": req.id,
                    "code": created_auth.plaintext_code,
                },
            )
        except ToolError as error:
            return error

    results = await asyncio.gather(call_cancel(), call_cancel())
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    failure_msg = str(failures[0])
    assert (
        "invalid_or_expired_code" in failure_msg
        or "cancellation_request_already_consumed" in failure_msg
    )


@pytest.mark.asyncio
async def test_mcp_cancel_authorized_order_unknown_outcome_directs_to_reconcile(
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

    execution_provider.cancel_order = AsyncMock(
        side_effect=ExecutionIndeterminateError("Gateway timeout during cancel")
    )

    server = create_server(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        execution_provider=execution_provider,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    req = cancellation_request_service.create_request(order_id)
    _, created_auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )

    res = await server.call_tool(
        "cancel_authorized_order",
        {
            "cancellation_request_id": req.id,
            "code": created_auth.plaintext_code,
        },
    )
    assert isinstance(res, types.CallToolResult)
    order_data = res.model_dump()
    text = order_data["content"][0]["text"]
    assert "unknown" in text.lower()
    assert "reconciliation" in text.lower()

    order = repo.order(order_id)
    assert order is not None
    assert order.state == OrderState.UNKNOWN

    saved_req = cancellation_request_service.get_request(req.id)
    assert saved_req is not None
    assert saved_req.status == "attempted"

    with pytest.raises(
        ToolError,
        match="(invalid_or_expired_code|cancellation_request_already_consumed)",
    ):
        await server.call_tool(
            "cancel_authorized_order",
            {
                "cancellation_request_id": req.id,
                "code": created_auth.plaintext_code,
            },
        )

    events = repo.list_order_events(order_id=order_id, limit=50).items
    consumed_events = [
        e
        for e in events
        if e.event_type == OrderEventType.AUTHORIZATION_CONSUMED
        and e.details.get("action") == "cancel"
    ]
    assert len(consumed_events) == 1
    assert consumed_events[0].actor == OrderEventActor.MCP


@pytest.mark.asyncio
async def test_mcp_cancel_authorized_order_refusal_retry_prevented(
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

    order_before = repo.order(order_id)
    assert order_before is not None

    execution_provider.cancel_order = AsyncMock(
        return_value=ExecutionResult(
            state=OrderState.ACCEPTED,
            broker_order_id=order_before.broker_order_id,
            message="Cancel rejected: order in execution",
        )
    )

    server = create_server(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        execution_provider=execution_provider,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    req = cancellation_request_service.create_request(order_id)
    _, created_auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )

    res = await server.call_tool(
        "cancel_authorized_order",
        {
            "cancellation_request_id": req.id,
            "code": created_auth.plaintext_code,
        },
    )
    assert isinstance(res, types.CallToolResult)
    order_data = res.model_dump()
    text = order_data["content"][0]["text"]
    assert "cancel_rejected" in text.lower() or "rejected" in text.lower()
    assert "canceled" not in text.lower() or 'state": "accepted"' in text.lower()

    order = repo.order(order_id)
    assert order is not None
    assert order.state == OrderState.ACCEPTED
    assert order.result_code == "cancel_rejected"

    saved_req = cancellation_request_service.get_request(req.id)
    assert saved_req is not None
    assert saved_req.status == "attempted"

    with pytest.raises(
        ToolError,
        match="(invalid_or_expired_code|cancellation_request_already_consumed)",
    ):
        await server.call_tool(
            "cancel_authorized_order",
            {
                "cancellation_request_id": req.id,
                "code": created_auth.plaintext_code,
            },
        )

    events = repo.list_order_events(order_id=order_id, limit=50).items
    consumed_events = [
        e
        for e in events
        if e.event_type == OrderEventType.AUTHORIZATION_CONSUMED
        and e.details.get("action") == "cancel"
    ]
    assert len(consumed_events) == 1
    assert consumed_events[0].actor == OrderEventActor.MCP


@pytest.mark.asyncio
async def test_mcp_cancel_authorized_order_state_changed_fails(tmp_path) -> None:
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

    server = create_server(
        provider=provider,
        database_url=db_url,
        market_data_provider=market_data,
        execution_provider=execution_provider,
        cancellation_service=cancellation_service,
        cancellation_request_service=cancellation_request_service,
        submission_service=submission_service,
        mcp_auth_service=mcp_auth_service,
        clock=lambda: now,
    )

    req = cancellation_request_service.create_request(order_id)
    _, created_auth = mcp_auth_service.authorize_cancellation_request(
        req.id, expected_fingerprint=req.action_fingerprint
    )

    with repo._sessions() as session:
        from portfolio_mcp.schema import OrderRecord

        rec = session.get(OrderRecord, order_id)
        assert rec is not None
        rec.state = OrderState.FILLED.value
        rec.version += 1
        session.commit()

    with pytest.raises(ToolError, match="(order_conflict|order_not_cancelable)"):
        await server.call_tool(
            "cancel_authorized_order",
            {
                "cancellation_request_id": req.id,
                "code": created_auth.plaintext_code,
            },
        )
