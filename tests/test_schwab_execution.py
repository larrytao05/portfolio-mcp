from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from portfolio_mcp.config import SchwabSettings
from portfolio_mcp.database import (
    AccountRecord,
    OrderDraftRecord,
    OrderRecord,
    PortfolioRepository,
)
from portfolio_mcp.execution import (
    CapabilityState,
    ExecutionCommand,
    ExecutionError,
    ExecutionIndeterminateError,
    OrderState,
)
from portfolio_mcp.order_reconciliation import OrderReconciliationService
from portfolio_mcp.provider import ProviderUnavailableError
from portfolio_mcp.schwab_execution import SchwabExecutionProvider
from portfolio_mcp.schwab_transport import SchwabHttpClient, SchwabOAuthTransport


class FakeExecutionHttpClient(SchwabHttpClient):
    def __init__(
        self,
        responses: list[
            tuple[int, object, dict[str, str]] | tuple[int, object] | Exception
        ],
    ) -> None:
        self._responses = list(responses)
        self.invocations: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        status, resp_body, _ = self.request_with_headers(method, url, headers, body)
        return status, resp_body

    def request_with_headers(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object, dict[str, str]]:
        self.invocations.append((method, url, headers, body))
        if not self._responses:
            raise RuntimeError(f"No response configured for {method} {url}")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        if len(resp) == 2:
            return resp[0], resp[1], {}
        return resp  # type: ignore


def _setup_repo(tmp_path: Path) -> PortfolioRepository:
    db_file = tmp_path / "schwab_exec_test.db"
    repo = PortfolioRepository(f"sqlite:///{db_file}")
    now = datetime.now(UTC)
    with repo._sessions() as s:
        s.add(
            AccountRecord(
                id="schwab-acc-1",
                provider="schwab",
                label="Schwab Individual ••••1234",
                account_type="Taxable brokerage",
                currency="USD",
                refreshed_at=now,
                is_stale=False,
            )
        )
        s.commit()
    repo.save_schwab_account_mapping("schwab-acc-1", "hash-secret-1234", "*1234")
    return repo


def _make_provider(
    repo: PortfolioRepository,
    responses: list[Any],
    clock: Any = None,
    gate_seconds: float = 30.0,
) -> tuple[SchwabExecutionProvider, FakeExecutionHttpClient]:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeExecutionHttpClient(responses)
    transport = SchwabOAuthTransport(settings, http_client=client)
    provider = SchwabExecutionProvider(
        transport=transport,
        repository=repo,
        clock=clock,
        recent_orders_gate_seconds=gate_seconds,
    )
    return provider, client


# --- 1. Capability & Unmapped Tests ---


@pytest.mark.asyncio
async def test_capability_mapped_and_unmapped(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    provider, _ = _make_provider(repo, [])

    # Mapped account is supported
    cap = await provider.get_execution_capability("schwab-acc-1")
    assert cap.state == CapabilityState.SUPPORTED
    assert cap.can_submit is True
    assert cap.can_cancel is True

    # Unmapped account is unsupported
    cap_unmapped = await provider.get_execution_capability("unmapped-acc")
    assert cap_unmapped.state == CapabilityState.UNSUPPORTED
    assert cap_unmapped.can_submit is False
    assert cap_unmapped.can_cancel is False


@pytest.mark.asyncio
async def test_unmapped_account_submit_fails_closed(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    provider, client = _make_provider(repo, [])

    cmd = ExecutionCommand(
        client_order_id="cmd-1",
        account_id="unmapped-acc",
        provider="schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="buy",
        order_type="limit",
        quantity=Decimal("10"),
        limit_price=Decimal("220.00"),
    )
    with pytest.raises(ExecutionError, match="not mapped"):
        await provider.submit_order(cmd)
    assert len(client.invocations) == 0


# --- 2. Order Serialization & Submit Tests ---


@pytest.mark.asyncio
async def test_submit_limit_order_success(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    responses = [
        # Token
        (200, {"access_token": "token-1", "expires_in": 1800}),
        # Order POST 201 Created with Location header
        (
            201,
            {},
            {
                "Location": "https://api.schwabapi.com/trader/v1/accounts/hash-secret-1234/orders/11223344"
            },
        ),
    ]
    provider, client = _make_provider(repo, responses)

    cmd = ExecutionCommand(
        client_order_id="cmd-limit-1",
        account_id="schwab-acc-1",
        provider="schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="buy",
        order_type="limit",
        quantity=Decimal("5"),
        limit_price=Decimal("225.50"),
    )

    result = await provider.submit_order(cmd)
    assert result.state == OrderState.ACCEPTED
    assert result.broker_order_id == "11223344"

    # Verify request method and opaque hash in URL
    assert len(client.invocations) == 2
    method, url, headers, body = client.invocations[1]
    assert method == "POST"
    assert "/accounts/hash-secret-1234/orders" in url
    assert "schwab-acc-1" not in url  # Never leaks local ID to broker path
    assert body is not None
    import json

    data = json.loads(body)
    assert data["orderType"] == "LIMIT"
    assert data["session"] == "NORMAL"
    assert data["duration"] == "DAY"
    assert data["orderStrategyType"] == "SINGLE"
    assert data["price"] == "225.50"
    assert data["orderLegCollection"][0]["instruction"] == "BUY"
    assert data["orderLegCollection"][0]["quantity"] == 5
    assert data["orderLegCollection"][0]["instrument"]["symbol"] == "VTI"
    assert data["orderLegCollection"][0]["instrument"]["assetType"] == "EQUITY"


@pytest.mark.asyncio
async def test_submit_market_order_serialization(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (201, {"orderId": 99887766}, {}),
    ]
    provider, client = _make_provider(repo, responses)

    cmd = ExecutionCommand(
        client_order_id="cmd-mkt-1",
        account_id="schwab-acc-1",
        provider="schwab",
        instrument_id="us-stock:AAPL",
        symbol="AAPL",
        side="sell",
        order_type="market",
        quantity=Decimal("15"),
        limit_price=None,
    )

    result = await provider.submit_order(cmd)
    assert result.state == OrderState.ACCEPTED
    assert result.broker_order_id == "99887766"

    _, _, _, body = client.invocations[1]
    assert body is not None
    import json

    data = json.loads(body)
    assert data["orderType"] == "MARKET"
    assert "price" not in data
    assert data["orderLegCollection"][0]["instruction"] == "SELL"
    assert data["orderLegCollection"][0]["quantity"] == 15


@pytest.mark.asyncio
async def test_submit_missing_broker_id_returns_unknown(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        # 201 Created but no Location header and empty body
        (201, {}, {}),
    ]
    provider, _ = _make_provider(repo, responses)

    cmd = ExecutionCommand(
        client_order_id="cmd-unknown-1",
        account_id="schwab-acc-1",
        provider="schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        limit_price=None,
    )

    result = await provider.submit_order(cmd)
    assert result.state == OrderState.UNKNOWN
    assert result.broker_order_id is None
    assert "reconciliation required" in str(result.message).lower()


@pytest.mark.asyncio
async def test_submit_rejection_returns_rejected(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (400, {"error": "Account has insufficient settled funds"}, {}),
    ]
    provider, _ = _make_provider(repo, responses)

    cmd = ExecutionCommand(
        client_order_id="cmd-rej-1",
        account_id="schwab-acc-1",
        provider="schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        limit_price=None,
    )

    result = await provider.submit_order(cmd)
    assert result.state == OrderState.REJECTED
    assert "insufficient settled funds" in str(result.message)


@pytest.mark.asyncio
async def test_submit_timeout_raises_indeterminate(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        TimeoutError("Connection timed out"),
    ]
    provider, _ = _make_provider(repo, responses)

    cmd = ExecutionCommand(
        client_order_id="cmd-timeout-1",
        account_id="schwab-acc-1",
        provider="schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        limit_price=None,
    )

    with pytest.raises(ExecutionIndeterminateError):
        await provider.submit_order(cmd)


@pytest.mark.asyncio
async def test_unsupported_commands_rejected_before_network(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    provider, client = _make_provider(repo, [])

    # Fractional quantity
    cmd_fractional = ExecutionCommand(
        client_order_id="cmd-frac",
        account_id="schwab-acc-1",
        provider="schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="buy",
        order_type="market",
        quantity=Decimal("2.5"),
        limit_price=None,
    )
    with pytest.raises(ExecutionError, match="whole-share"):
        await provider.submit_order(cmd_fractional)

    # Unsupported side
    cmd_side = ExecutionCommand(
        client_order_id="cmd-side",
        account_id="schwab-acc-1",
        provider="schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="short_sell",
        order_type="market",
        quantity=Decimal("5"),
        limit_price=None,
    )
    with pytest.raises(ExecutionError, match="side"):
        await provider.submit_order(cmd_side)

    # Missing limit price for limit order
    cmd_noprice = ExecutionCommand(
        client_order_id="cmd-noprice",
        account_id="schwab-acc-1",
        provider="schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="buy",
        order_type="limit",
        quantity=Decimal("5"),
        limit_price=None,
    )
    with pytest.raises(ExecutionError, match="Limit price"):
        await provider.submit_order(cmd_noprice)

    assert len(client.invocations) == 0


# --- 3. Cancellation Tests ---


@pytest.mark.asyncio
async def test_cancel_order_success(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (200, {}, {}),
    ]
    provider, client = _make_provider(repo, responses)

    res = await provider.cancel_order("broker-1234", account_id="schwab-acc-1")
    assert res.state == OrderState.CANCELED
    assert res.broker_order_id == "broker-1234"

    assert len(client.invocations) == 2
    method, url, _, _ = client.invocations[1]
    assert method == "DELETE"
    assert "/accounts/hash-secret-1234/orders/broker-1234" in url


@pytest.mark.asyncio
async def test_cancel_order_rejection(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (400, {"error": "Order is already filled and cannot be canceled"}, {}),
    ]
    provider, _ = _make_provider(repo, responses)

    res = await provider.cancel_order("broker-1234", account_id="schwab-acc-1")
    assert (
        res.state == OrderState.ACCEPTED
    )  # Not canceled; remains in last known accepted state
    assert "cannot be canceled" in str(res.message).lower()


# --- 4. Order Lookup & Status Normalization Tests ---


@pytest.mark.asyncio
async def test_find_by_broker_order_id_filled(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    now_str = "2026-09-26T12:00:00+0000"
    schwab_order = {
        "orderId": 123456,
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "price": 220.0,
        "quantity": 10.0,
        "filledQuantity": 10.0,
        "remainingQuantity": 0.0,
        "status": "FILLED",
        "enteredTime": now_str,
        "closeTime": now_str,
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 10.0,
                "instrument": {"symbol": "VTI", "assetType": "EQUITY"},
            }
        ],
        "orderActivityCollection": [
            {
                "activityType": "EXECUTION",
                "executionType": "FILL",
                "quantity": 10.0,
                "executionLegs": [{"price": 219.8, "quantity": 10.0}],
            }
        ],
    }
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (200, schwab_order, {}),
    ]
    provider, _ = _make_provider(repo, responses)

    snapshot = await provider.find_by_broker_order_id("schwab-acc-1", "123456")
    assert snapshot is not None
    assert snapshot.broker_order_id == "123456"
    assert snapshot.account_id == "schwab-acc-1"
    assert snapshot.state == OrderState.FILLED
    assert snapshot.quantity == Decimal("10")
    assert snapshot.fill is not None
    assert snapshot.fill.quantity == Decimal("10")
    assert snapshot.fill.average_price == Decimal("219.8")
    assert snapshot.side == "buy"
    assert snapshot.order_type == "limit"
    assert snapshot.status_label == "FILLED"


@pytest.mark.asyncio
async def test_find_by_broker_order_id_partial_fill(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    now_str = "2026-09-26T12:00:00+0000"
    schwab_order = {
        "orderId": 654321,
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "price": 220.0,
        "quantity": 10.0,
        "filledQuantity": 4.0,
        "remainingQuantity": 6.0,
        "status": "WORKING",
        "enteredTime": now_str,
        "orderLegCollection": [
            {
                "instruction": "SELL",
                "quantity": 10.0,
                "instrument": {"symbol": "VTI", "assetType": "EQUITY"},
            }
        ],
        "orderActivityCollection": [
            {
                "activityType": "EXECUTION",
                "executionType": "FILL",
                "quantity": 4.0,
                "executionLegs": [{"price": 220.0, "quantity": 4.0}],
            }
        ],
    }
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (200, schwab_order, {}),
    ]
    provider, _ = _make_provider(repo, responses)

    snapshot = await provider.find_by_broker_order_id("schwab-acc-1", "654321")
    assert snapshot is not None
    assert snapshot.state == OrderState.PARTIALLY_FILLED
    assert snapshot.status_label == "PARTIALLY_FILLED"
    assert snapshot.fill is not None
    assert snapshot.fill.quantity == Decimal("4")


# --- 5. Bounded Recent Orders & 30s Request Gate ---


@pytest.mark.asyncio
async def test_search_orders_enforces_30s_gate(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
    current_time = now

    def clock():
        return current_time

    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        # First search succeeds
        (200, [], {}),
        # Third search (after gate advances) succeeds
        (200, [], {}),
    ]
    provider, client = _make_provider(repo, responses, clock=clock, gate_seconds=30.0)

    # First search at t=0
    search1 = await provider.search_orders(
        "schwab-acc-1", now - timedelta(minutes=5), now
    )
    assert search1.complete is True
    assert len(client.invocations) == 2  # token + orders GET

    # Second search at t=10s (within 30s gate) should use cached
    # search without HTTP request
    current_time = now + timedelta(seconds=10)
    search2 = await provider.search_orders(
        "schwab-acc-1", now - timedelta(minutes=5), now
    )
    assert search2.complete is True
    assert len(client.invocations) == 2  # No new HTTP call made

    # Third search at t=35s (past 30s gate) makes a fresh HTTP request
    current_time = now + timedelta(seconds=35)
    search3 = await provider.search_orders(
        "schwab-acc-1", now - timedelta(minutes=5), now
    )
    assert search3.complete is True
    assert len(client.invocations) == 3  # Fresh HTTP call made


@pytest.mark.asyncio
async def test_find_by_broker_order_id_working_open(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    now_str = "2026-09-26T12:00:00+0000"
    schwab_order = {
        "orderId": 999111,
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "price": 220.0,
        "quantity": 10.0,
        "filledQuantity": 0.0,
        "remainingQuantity": 10.0,
        "status": "WORKING",
        "enteredTime": now_str,
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 10.0,
                "instrument": {"symbol": "VTI", "assetType": "EQUITY"},
            }
        ],
    }
    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (200, schwab_order, {}),
    ]
    provider, _ = _make_provider(repo, responses)

    snapshot = await provider.find_by_broker_order_id("schwab-acc-1", "999111")
    assert snapshot is not None
    assert snapshot.state == OrderState.ACCEPTED
    assert snapshot.status_label == "OPEN"
    assert snapshot.fill is None


@pytest.mark.asyncio
async def test_find_by_broker_order_id_error_handling(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)

    # 1. 404 returns None
    responses_404 = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (404, {}, {}),
    ]
    provider, _ = _make_provider(repo, responses_404)
    res_404 = await provider.find_by_broker_order_id("schwab-acc-1", "missing-id")
    assert res_404 is None

    # 2. 500 raises ExecutionError
    responses_500 = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (500, {"error": "Internal server error"}, {}),
    ]
    provider, _ = _make_provider(repo, responses_500)
    with pytest.raises(ExecutionError, match="status 500"):
        await provider.find_by_broker_order_id("schwab-acc-1", "err-id")

    # 3. Network error raises ProviderUnavailableError
    responses_net = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        ProviderUnavailableError("Network unreachable"),
    ]
    provider, _ = _make_provider(repo, responses_net)
    with pytest.raises(ProviderUnavailableError, match="unreachable"):
        await provider.find_by_broker_order_id("schwab-acc-1", "net-id")

    # 4. Unmapped account raises ExecutionError
    with pytest.raises(ExecutionError, match="not mapped"):
        await provider.find_by_broker_order_id("unmapped-acc", "any-id")


@pytest.mark.asyncio
async def test_search_orders_rate_gate_disjoint_window_returns_incomplete(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
    current_time = now

    def clock():
        return current_time

    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (200, [], {}),
    ]
    provider, client = _make_provider(repo, responses, clock=clock, gate_seconds=30.0)

    # First search at t=0 covering window [now - 5m, now]
    search1 = await provider.search_orders(
        "schwab-acc-1", now - timedelta(minutes=5), now
    )
    assert search1.complete is True
    assert len(client.invocations) == 2

    # Second search at t=10s querying a disjoint window [now - 15m, now - 10m]
    current_time = now + timedelta(seconds=10)
    search2 = await provider.search_orders(
        "schwab-acc-1", now - timedelta(minutes=15), now - timedelta(minutes=10)
    )
    assert search2.complete is False
    assert search2.orders == ()
    assert len(client.invocations) == 2  # No new HTTP call made, rate-gate enforced


@pytest.mark.asyncio
async def test_reconcile_schwab_order_e2e(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S+0000")

    # Seed an order in ACCEPTED state
    with repo._sessions() as s:
        s.add(
            OrderDraftRecord(
                id="draft-e2e-1",
                account_id="schwab-acc-1",
                account_label="Schwab Individual ••••1234",
                provider="schwab",
                instrument_id="us-etf:VTI",
                symbol="VTI",
                instrument_name="Vanguard Total Stock Market ETF",
                asset_class="etf",
                side="buy",
                order_type="limit",
                quantity=Decimal("10"),
                limit_price=Decimal("220.0"),
                warnings="[]",
                fingerprint="fp123",
                created_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )
        s.add(
            OrderRecord(
                id="order-e2e-1",
                draft_id="draft-e2e-1",
                client_order_id="client-e2e-1",
                fingerprint="fp123",
                account_id="schwab-acc-1",
                account_label="Schwab Individual ••••1234",
                provider="schwab",
                instrument_id="us-etf:VTI",
                symbol="VTI",
                side="buy",
                order_type="limit",
                quantity=Decimal("10"),
                limit_price=Decimal("220.0"),
                state="ACCEPTED",
                broker_order_id="888777",
                result_code="submitted",
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
        s.commit()

    schwab_filled_order = {
        "orderId": 888777,
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "price": 220.0,
        "quantity": 10.0,
        "filledQuantity": 10.0,
        "remainingQuantity": 0.0,
        "status": "FILLED",
        "enteredTime": now_str,
        "closeTime": now_str,
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 10.0,
                "instrument": {"symbol": "VTI", "assetType": "EQUITY"},
            }
        ],
        "orderActivityCollection": [
            {
                "activityType": "EXECUTION",
                "executionType": "FILL",
                "quantity": 10.0,
                "executionLegs": [{"price": 219.5, "quantity": 10.0}],
            }
        ],
    }

    responses = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (200, schwab_filled_order, {}),
    ]
    provider, _ = _make_provider(repo, responses)
    reconciliation = OrderReconciliationService(repo, provider, lambda: now)

    result = await reconciliation.refresh("order-e2e-1")
    assert result.status == "attempted"
    assert result.order.state == OrderState.FILLED
    assert result.order.filled_quantity == Decimal("10")
    assert result.order.average_fill_price == Decimal("219.5")
    assert result.order.provider_status_label == "FILLED"

    # Test working Schwab order reconciling to ACCEPTED / "OPEN"
    with repo._sessions() as s:
        s.add(
            OrderDraftRecord(
                id="draft-e2e-2",
                account_id="schwab-acc-1",
                account_label="Schwab Individual ••••1234",
                provider="schwab",
                instrument_id="us-etf:VTI",
                symbol="VTI",
                instrument_name="Vanguard Total Stock Market ETF",
                asset_class="etf",
                side="buy",
                order_type="limit",
                quantity=Decimal("10"),
                limit_price=Decimal("220.0"),
                warnings="[]",
                fingerprint="fp123",
                created_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )
        s.add(
            OrderRecord(
                id="order-e2e-2",
                draft_id="draft-e2e-2",
                client_order_id="client-e2e-2",
                fingerprint="fp123",
                account_id="schwab-acc-1",
                account_label="Schwab Individual ••••1234",
                provider="schwab",
                instrument_id="us-etf:VTI",
                symbol="VTI",
                side="buy",
                order_type="limit",
                quantity=Decimal("10"),
                limit_price=Decimal("220.0"),
                state="ACCEPTED",
                broker_order_id="555444",
                result_code="submitted",
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
        s.commit()

    now_working = now + timedelta(seconds=35)
    now_working_str = now_working.strftime("%Y-%m-%dT%H:%M:%S+0000")
    schwab_working_order = {
        "orderId": 555444,
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "price": 220.0,
        "quantity": 10.0,
        "filledQuantity": 0.0,
        "remainingQuantity": 10.0,
        "status": "WORKING",
        "enteredTime": now_working_str,
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 10.0,
                "instrument": {"symbol": "VTI", "assetType": "EQUITY"},
            }
        ],
    }

    responses_working = [
        (200, {"access_token": "token-1", "expires_in": 1800}),
        (200, schwab_working_order, {}),
    ]
    provider_working, _ = _make_provider(repo, responses_working)
    reconciliation_working = OrderReconciliationService(
        repo,
        provider_working,
        lambda: now_working,
    )
    result_working = await reconciliation_working.refresh("order-e2e-2")
    assert result_working.status == "attempted"
    assert result_working.order.state == OrderState.ACCEPTED
    assert result_working.order.provider_status_label == "OPEN"
