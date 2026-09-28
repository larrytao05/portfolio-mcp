import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.database import CancellationObservation, PortfolioRepository
from portfolio_mcp.execution import (
    ExecutionResult,
    FillSummary,
    FixtureExecutionProvider,
    OrderState,
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


def test_accepted_cancellation_refusal_with_known_fill_becomes_unknown(
    tmp_path,
) -> None:
    class AcceptedWithoutFillExecutionProvider(FixtureExecutionProvider):
        def __init__(self) -> None:
            super().__init__(scenario="partial_fill")

        async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
            return ExecutionResult(OrderState.ACCEPTED, broker_order_id)

    client = _client(tmp_path, AcceptedWithoutFillExecutionProvider())
    order = _submit_order(client)
    assert order["state"] == "PARTIALLY_FILLED"
    assert cast(dict[str, object], order["fill"])["quantity"] == "0.5"

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
    assert updated_order["state"] == "UNKNOWN"
    assert updated_order["can_cancel"] is False
    assert cast(dict[str, object], updated_order["fill"])["quantity"] == "0.5"


def test_accepted_cancellation_refusal_preserves_new_valid_fill(tmp_path) -> None:
    class AcceptedWithFillExecutionProvider(FixtureExecutionProvider):
        async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
            return ExecutionResult(
                OrderState.ACCEPTED,
                broker_order_id,
                fill=FillSummary(Decimal("0.5"), Decimal("100")),
            )

    client = _client(tmp_path, AcceptedWithFillExecutionProvider())
    order = _submit_order(client)
    assert order["state"] == "ACCEPTED"

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
    assert updated_order["state"] == "UNKNOWN"
    assert updated_order["can_cancel"] is False
    fill = cast(dict[str, object], updated_order["fill"])
    assert fill["quantity"] == "0.5"
    assert fill["average_price"] == "100"


def test_canceled_result_with_full_fill_becomes_unknown_and_keeps_fill(
    tmp_path,
) -> None:
    class CanceledWithFullFillExecutionProvider(FixtureExecutionProvider):
        async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
            return ExecutionResult(
                OrderState.CANCELED,
                broker_order_id,
                fill=FillSummary(Decimal("1"), Decimal("100")),
            )

    client = _client(tmp_path, CanceledWithFullFillExecutionProvider())
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
    assert updated_order["state"] == "UNKNOWN"
    assert updated_order["can_cancel"] is False
    fill = cast(dict[str, object], updated_order["fill"])
    assert fill["quantity"] == "1"
    assert fill["average_price"] == "100"


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


def test_cancellation_returned_unknown_result_stays_unknown_and_noncancelable(
    tmp_path,
) -> None:
    class UnknownReturningExecutionProvider(FixtureExecutionProvider):
        async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
            return ExecutionResult(
                OrderState.UNKNOWN, broker_order_id, "INTERNAL_BROKER_TEXT"
            )

    execution = UnknownReturningExecutionProvider()
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
    assert "INTERNAL_BROKER_TEXT" not in (unknown_order["result"]["message"] or "")

    audit_response = client.get(f"/api/order-audit?order_id={order['id']}")
    audit_text = json.dumps(audit_response.json())
    assert "INTERNAL_BROKER_TEXT" not in audit_text


def test_cancellation_bad_or_regressing_fill_commits_unknown_and_preserves_prior_fill(
    tmp_path,
) -> None:
    class RegressingFillExecutionProvider(FixtureExecutionProvider):
        def __init__(self) -> None:
            super().__init__(scenario="partial_fill")

        async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
            return ExecutionResult(
                OrderState.CANCELED,
                broker_order_id,
                fill=FillSummary(Decimal("0.2"), Decimal("100")),
            )

    execution = RegressingFillExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)
    assert order["state"] == "PARTIALLY_FILLED"
    assert cast(dict[str, object], order["fill"])["quantity"] == "0.5"

    response = client.post(
        f"/api/orders/{order['id']}/cancel/confirm",
        json={
            "expected_version": order["version"],
            "expected_state": order["state"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    res_order = response.json()["order"]
    assert res_order["state"] == "UNKNOWN"
    assert res_order["can_cancel"] is False
    # Preserves prior valid fill
    assert res_order["fill"]["quantity"] == "0.5"


def test_cancellation_provider_exception_text_never_leaks_to_db_or_audit(
    tmp_path,
) -> None:
    class LeakyExceptionExecutionProvider(FixtureExecutionProvider):
        async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
            raise RuntimeError("CRITICAL_BROKER_LEAK_SECRET_KEY")

    execution = LeakyExceptionExecutionProvider()
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
    res_order = response.json()["order"]
    assert res_order["state"] == "UNKNOWN"
    assert "CRITICAL_BROKER_LEAK_SECRET_KEY" not in (
        res_order["result"]["message"] or ""
    )

    audit_response = client.get(f"/api/order-audit?order_id={order['id']}")
    audit_text = json.dumps(audit_response.json())
    assert "CRITICAL_BROKER_LEAK_SECRET_KEY" not in audit_text


def test_cancellation_late_obsolete_attempt_rejected(tmp_path) -> None:
    execution = FixtureExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)
    order_id = cast(str, order["id"])
    version = cast(int, order["version"])
    state = OrderState(cast(str, order["state"]))
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'cancellation.db'}")
    now = datetime.now(UTC)
    stored, attempt_id, started = repository.begin_order_cancellation(
        order_id,
        expected_version=version,
        expected_state=state,
        now=now,
    )
    assert started is True
    assert attempt_id is not None

    fake_attempt = uuid4()
    res_obsolete = repository.finish_order_cancellation(
        order_id,
        attempt_id=fake_attempt,
        observation=CancellationObservation(kind="canceled"),
        now=now,
    )
    assert res_obsolete.state == OrderState.CANCEL_PENDING

    res_legit = repository.finish_order_cancellation(
        order_id,
        attempt_id=attempt_id,
        observation=CancellationObservation(kind="canceled"),
        now=now,
    )
    assert res_legit.state == OrderState.CANCELED


def test_repository_begin_order_cancellation_claim_race_safety(tmp_path) -> None:
    execution = FixtureExecutionProvider()
    client = _client(tmp_path, execution)
    order = _submit_order(client)
    order_id = cast(str, order["id"])
    version = cast(int, order["version"])
    state = OrderState(cast(str, order["state"]))
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'cancellation.db'}")
    now = datetime.now(UTC)

    stored1, attempt1, started1 = repository.begin_order_cancellation(
        order_id,
        expected_version=version,
        expected_state=state,
        now=now,
    )
    assert started1 is True
    assert stored1.state == OrderState.CANCEL_PENDING
    assert attempt1 is not None

    stored2, attempt2, started2 = repository.begin_order_cancellation(
        order_id,
        expected_version=stored1.version,
        expected_state=OrderState.CANCEL_PENDING,
        now=now,
    )
    assert started2 is False
    assert attempt2 == attempt1
    assert stored2.state == OrderState.CANCEL_PENDING
