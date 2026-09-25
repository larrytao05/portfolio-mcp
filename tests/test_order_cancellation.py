import asyncio
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.execution import (
    ExecutionResult,
    FixtureExecutionProvider,
)
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.trading_service import allow_fixture_submission


def _app(
    tmp_path,
    execution: FixtureExecutionProvider,
    now: datetime | None = None,
):
    if now is None:
        now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    return create_app(
        FixturePortfolioProvider(),
        execution_provider=execution,
        submission_validator=allow_fixture_submission,
        database_url=f"sqlite:///{tmp_path / 'cancellation.db'}",
        clock=lambda: now,
    )


def _client(
    tmp_path,
    execution: FixtureExecutionProvider,
) -> TestClient:
    app = _app(tmp_path, execution)
    client = TestClient(app)
    assert client.post("/api/refresh").status_code == 200
    settings = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": True,
            "kill_switch_active": False,
            "max_order_shares": "10",
            "max_order_notional_usd": "10000",
            "version": 0,
        },
    )
    assert settings.status_code == 200
    return client


def _submit_order(client: TestClient) -> dict[str, object]:
    draft_response = client.post(
        "/api/order-drafts",
        json={
            "account_id": "schwab-taxable-demo",
            "instrument_id": "us-etf:VTI",
            "side": "buy",
            "order_type": "limit",
            "quantity": "1",
            "limit_price": "300.25",
        },
    )
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()["draft"]
    response = client.post(
        f"/api/order-drafts/{draft['id']}/confirm",
        json={"expected_fingerprint": draft["fingerprint"], "confirmed": True},
    )
    assert response.status_code == 200
    return response.json()["order"]


def test_order_read_model_exposes_can_cancel_and_blocking_reason(tmp_path) -> None:
    execution = FixtureExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    # An open accepted order with broker ID is cancelable
    assert order["state"] == "ACCEPTED"
    assert order["can_cancel"] is True
    assert order["blocking_reason"] is None

    # Cancel the order
    cancel_response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert cancel_response.status_code == 200
    canceled_order = cancel_response.json()["order"]
    assert canceled_order["state"] == "CANCELED"
    assert canceled_order["can_cancel"] is False
    assert canceled_order["blocking_reason"] == "Order is already canceled"


def test_cancellation_requires_explicit_confirmation(tmp_path) -> None:
    execution = FixtureExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": False,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "confirmation_required"

    # Provider cancel was never called
    assert not any(action == "cancel" for action, _ in execution.invocations)


def test_cancellation_rejects_noncancelable_or_stale_version_orders(tmp_path) -> None:
    execution = FixtureExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    # Stale version returns 409
    response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": cast(int, order["version"]) + 10,
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "order_conflict"

    # Nonexistent order returns 404
    missing_response = client.post(
        "/api/orders/non-existent-order-id/cancel/confirm",
        json={
            "expected_version": 1,
            "expected_state": "ACCEPTED",
            "confirmed": True,
        },
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "order_not_found"


def test_cancellation_allowed_under_kill_switch(tmp_path) -> None:
    execution = FixtureExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    # Activate kill switch
    settings = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": True,
            "kill_switch_active": True,
            "max_order_shares": "10",
            "max_order_notional_usd": "10000",
            "version": 1,
        },
    )
    assert settings.status_code == 200

    # Cancellation MUST still succeed
    response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    canceled = response.json()["order"]
    assert canceled["state"] == "CANCELED"
    assert any(action == "cancel" for action, _ in execution.invocations)


def test_cancellation_records_authorization_and_audit_events(tmp_path) -> None:
    execution = FixtureExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    cancel_response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert cancel_response.status_code == 200

    audit_response = client.get(f"/api/order-audit?order_id={order['id']}")
    assert audit_response.status_code == 200
    event_types = [event["type"] for event in audit_response.json()["events"]]

    assert "authorization_created" in event_types
    assert "authorization_consumed" in event_types
    assert "cancellation_requested" in event_types
    assert "cancellation_result" in event_types

    # Find the cancellation events
    events = audit_response.json()["events"]
    req_event = next(e for e in events if e["type"] == "cancellation_requested")
    res_event = next(e for e in events if e["type"] == "cancellation_result")

    assert req_event["details"]["attempt_id"] is not None
    assert res_event["details"]["outcome"] == "canceled"
    assert res_event["details"]["attempt_id"] == req_event["details"]["attempt_id"]


def test_cancellation_concurrency_and_replay_calls_provider_at_most_once(
    tmp_path,
) -> None:
    execution = FixtureExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    # First cancel
    res1 = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert res1.status_code == 200
    assert res1.json()["order"]["state"] == "CANCELED"

    # Replay of the exact same request
    res2 = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert res2.status_code == 200
    assert res2.json()["order"]["state"] == "CANCELED"

    # Verify provider was called at most once
    cancel_calls = [inv for inv in execution.invocations if inv[0] == "cancel"]
    assert len(cancel_calls) == 1


def test_cancellation_provider_rejection_retains_prior_state(tmp_path) -> None:
    execution = FixtureExecutionProvider(scenario="cancel_rejected")
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    updated_order = response.json()["order"]
    # Retains prior ACCEPTED state
    assert updated_order["state"] == "ACCEPTED"
    assert updated_order["can_cancel"] is True

    # Audit records refused outcome
    audit_response = client.get(f"/api/order-audit?order_id={order['id']}")
    res_event = next(
        e for e in audit_response.json()["events"] if e["type"] == "cancellation_result"
    )
    assert res_event["details"]["outcome"] == "refused"


def test_cancellation_unknown_enters_reconciliation_without_automatic_retry(
    tmp_path,
) -> None:
    execution = FixtureExecutionProvider(scenario="cancel_unknown")
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    unknown_order = response.json()["order"]
    assert unknown_order["state"] == "UNKNOWN"
    assert unknown_order["can_cancel"] is False

    # Provider was called once, no auto-retry
    cancel_calls = [inv for inv in execution.invocations if inv[0] == "cancel"]
    assert len(cancel_calls) == 1

    # Audit records unknown outcome
    audit_response = client.get(f"/api/order-audit?order_id={order['id']}")
    res_event = next(
        e for e in audit_response.json()["events"] if e["type"] == "cancellation_result"
    )
    assert res_event["details"]["outcome"] == "unknown"


def test_cancellation_partially_filled_order_preserves_fills(tmp_path) -> None:
    execution = FixtureExecutionProvider(scenario="partial_fill")
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    # Order is partially filled
    assert order["state"] == "PARTIALLY_FILLED"
    fill = cast(dict[str, object], order["fill"])
    assert fill["quantity"] == "0.5"
    assert order["can_cancel"] is True

    # Cancel the remaining 0.5 shares
    response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    canceled_order = response.json()["order"]
    assert canceled_order["state"] == "CANCELED"
    # Preserves filled quantity
    canceled_fill = cast(dict[str, object], canceled_order["fill"])
    assert canceled_fill["quantity"] == "0.5"
    assert canceled_fill["average_price"] == "100"


@pytest.mark.asyncio
async def test_concurrent_cancellations_call_provider_at_most_once(tmp_path) -> None:
    class SlowExecutionProvider(FixtureExecutionProvider):
        async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
            await asyncio.sleep(0.05)
            return await super().cancel_order(broker_order_id)

    execution = SlowExecutionProvider()
    app = _app(tmp_path, execution)
    client = TestClient(app)
    assert client.post("/api/refresh").status_code == 200
    settings = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": True,
            "kill_switch_active": False,
            "max_order_shares": "10",
            "max_order_notional_usd": "10000",
            "version": 0,
        },
    )
    assert settings.status_code == 200
    order = _submit_order(client)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as async_client:

        async def send_cancel() -> httpx.Response:
            return await async_client.post(
                f"/api/orders/{order['id']}/cancel/confirm",
                json={
                    "expected_version": order["version"],
                    "expected_state": order["state"],
                    "confirmed": True,
                },
            )

        responses = await asyncio.gather(*[send_cancel() for _ in range(10)])

    for res in responses:
        assert res.status_code == 200
        assert res.json()["order"]["state"] == "CANCELED"

    cancel_calls = [inv for inv in execution.invocations if inv[0] == "cancel"]
    assert len(cancel_calls) == 1
