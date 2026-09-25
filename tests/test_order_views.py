from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.execution import FixtureExecutionProvider
from portfolio_mcp.fixtures import FixturePortfolioProvider


def _client(
    tmp_path,
    execution: FixtureExecutionProvider,
    clock: Callable[[], datetime] | None = None,
) -> TestClient:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    service_clock = clock if clock is not None else (lambda: now)
    client = TestClient(
        create_app(
            FixturePortfolioProvider(),
            execution_provider=execution,
            database_url=f"sqlite:///{tmp_path / 'orders.db'}",
            clock=service_clock,
        )
    )
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


def test_orders_endpoint_returns_saved_page_and_bound_cursor(tmp_path) -> None:
    execution = FixtureExecutionProvider("accepted")
    client = _client(tmp_path, execution)
    first = _submit_order(client)
    second = _submit_order(client)

    page = client.get("/api/orders", params={"limit": 1, "state": "ACCEPTED"})

    assert page.status_code == 200
    body = page.json()
    assert len(body["orders"]) == 1
    assert body["orders"][0]["state"] == "ACCEPTED"
    assert body["orders"][0]["id"] in {first["id"], second["id"]}
    assert body["next_cursor"]
    assert body["server_time"] == "2026-09-12T20:00:00+00:00"

    next_page = client.get(
        "/api/orders",
        params={"limit": 1, "state": "ACCEPTED", "cursor": body["next_cursor"]},
    )
    assert next_page.status_code == 200
    assert len(next_page.json()["orders"]) == 1
    assert next_page.json()["orders"][0]["id"] != body["orders"][0]["id"]

    mismatched = client.get(
        "/api/orders",
        params={"limit": 1, "state": "UNKNOWN", "cursor": body["next_cursor"]},
    )
    assert mismatched.status_code == 422


def test_order_audit_is_global_filtered_and_unknown_order_is_not_found(
    tmp_path,
) -> None:
    client = _client(tmp_path, FixtureExecutionProvider("accepted"))
    order = _submit_order(client)

    order_id = str(order["id"])
    page = client.get(
        "/api/order-audit",
        params={"order_id": order_id, "account_id": "schwab-taxable-demo"},
    )

    assert page.status_code == 200
    events = page.json()["events"]
    assert events
    assert {event["draft_id"] for event in events} == {order["draft_id"]}
    assert all(
        "authorization_id" in event["details"]
        or event["type"]
        not in {
            "authorization_created",
            "authorization_consumed",
        }
        for event in events
    )

    missing_order = client.get("/api/orders/does-not-exist")
    assert missing_order.status_code == 404
    assert missing_order.json() == {"detail": "Order not found"}

    missing_scope = client.get(
        "/api/order-audit", params={"order_id": "does-not-exist"}
    )
    assert missing_scope.status_code == 404
    assert missing_scope.json() == {"detail": "Order not found"}


def test_refresh_is_explicit_and_returns_shared_gate_schedule(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    execution = FixtureExecutionProvider("accepted")
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    saved = client.get("/api/orders")
    assert saved.status_code == 200
    assert not any(name.startswith("read_") for name, _ in execution.invocations)

    response = client.post(
        f"/api/orders/{order['id']}/refresh", json={"mode": "manual"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["refresh"]["status"] == "attempted"
    assert body["refresh"]["provider_read_started"] is True
    assert (
        body["refresh"]["next_refresh_at"] == (now + timedelta(seconds=30)).isoformat()
    )

    throttled = client.post(
        f"/api/orders/{order['id']}/refresh", json={"mode": "manual"}
    )
    assert throttled.status_code == 200
    assert throttled.json()["refresh"]["status"] == "throttled"
    assert throttled.json()["refresh"]["provider_read_started"] is False


def test_orders_get_never_invokes_provider_reads(tmp_path) -> None:
    execution = FixtureExecutionProvider("accepted")
    client = _client(tmp_path, execution)
    _submit_order(client)
    _submit_order(client)

    initial_invocations = list(execution.invocations)
    res = client.get("/api/orders", params={"state": "ACCEPTED", "limit": 10})
    assert res.status_code == 200
    assert execution.invocations == initial_invocations
    assert not any(name.startswith("read_") for name, _ in execution.invocations)


def test_scheduled_target_fairness_across_pages(tmp_path) -> None:
    clock_time = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)

    def ticking_clock() -> datetime:
        nonlocal clock_time
        clock_time += timedelta(seconds=1)
        return clock_time

    execution = FixtureExecutionProvider("accepted", clock=ticking_clock)
    client = _client(tmp_path, execution, clock=ticking_clock)
    first = _submit_order(client)
    second = _submit_order(client)

    # Fetch page with limit=1. Descending created_at order returns second order.
    page1 = client.get("/api/orders", params={"limit": 1})
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["orders"]) == 1
    assert body1["orders"][0]["id"] == second["id"]

    # Even though page 1 only contains 'second', the server refresh plan targets
    # 'first' because 'first' is the earliest unattempted order in the account group.
    refresh_groups = body1["refresh_groups"]
    assert len(refresh_groups) == 1
    assert refresh_groups[0]["target_order_id"] == first["id"]

    # Scheduled refresh on the planned target succeeds
    refresh1 = client.post(
        f"/api/orders/{first['id']}/refresh", json={"mode": "scheduled"}
    )
    assert refresh1.status_code == 200
    assert refresh1.json()["refresh"]["status"] == "attempted"


def test_scheduled_refresh_rejects_target_change(tmp_path) -> None:
    clock_time = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)

    def ticking_clock() -> datetime:
        nonlocal clock_time
        clock_time += timedelta(seconds=1)
        return clock_time

    execution = FixtureExecutionProvider("accepted", clock=ticking_clock)
    client = _client(tmp_path, execution, clock=ticking_clock)
    first = _submit_order(client)
    second = _submit_order(client)

    # Server plans 'first' as fair target (created earlier).
    # Scheduled refresh on 'second' returns target_changed.
    mismatched = client.post(
        f"/api/orders/{second['id']}/refresh", json={"mode": "scheduled"}
    )
    assert mismatched.status_code == 200
    body = mismatched.json()["refresh"]
    assert body["status"] == "target_changed"
    assert body["provider_read_started"] is False
    assert body["target_order_id"] == first["id"]


def test_reconciliation_never_calls_submission_or_cancellation(tmp_path) -> None:
    execution = FixtureExecutionProvider("accepted")
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    submit_count_before = sum(
        1 for name, _ in execution.invocations if name == "submit"
    )
    cancel_count_before = sum(
        1 for name, _ in execution.invocations if name == "cancel"
    )

    refresh = client.post(f"/api/orders/{order['id']}/refresh", json={"mode": "manual"})
    assert refresh.status_code == 200

    submit_count_after = sum(1 for name, _ in execution.invocations if name == "submit")
    cancel_count_after = sum(1 for name, _ in execution.invocations if name == "cancel")
    assert submit_count_after == submit_count_before
    assert cancel_count_after == cancel_count_before


def test_malformed_audit_details_returns_safe_500(tmp_path) -> None:
    import sqlite3
    import uuid

    execution = FixtureExecutionProvider("accepted")
    client = _client(tmp_path, execution)
    order = _submit_order(client)

    # Insert a corrupted order event row with illegal details
    db_path = tmp_path / "orders.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO order_events (
            event_id, draft_id, order_id, account_id, event_type, actor,
            previous_state, next_state, code, details_schema_version,
            details_json, deduplication_key, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            str(order["draft_id"]),
            str(order["id"]),
            "schwab-taxable-demo",
            "status_transition",
            "system",
            "SUBMITTING",
            "ACCEPTED",
            None,
            1,
            '{"illegal_leak": "secret"}',
            str(uuid.uuid4()),
            "2026-09-12 20:00:01",
        ),
    )
    conn.commit()
    conn.close()

    res = client.get("/api/order-audit", params={"order_id": str(order["id"])})
    assert res.status_code == 500
    assert res.json() == {"detail": "Order audit details are unavailable"}

    invalid_limit = client.get("/api/order-audit", params={"limit": 0})
    assert invalid_limit.status_code == 422
