from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from portfolio_mcp.alpaca_execution import AlpacaExecutionProvider
from portfolio_mcp.alpaca_market_data import AlpacaMarketDataProvider
from portfolio_mcp.alpaca_portfolio import AlpacaPortfolioProvider
from portfolio_mcp.api import create_app
from portfolio_mcp.config import AlpacaMarketDataSettings

NOW = datetime(2026, 11, 10, 16, tzinfo=UTC)


class FakeHttp:
    def __init__(self):
        self.writes = []
        self.order_response: tuple[int, object] | None = None

    def request(self, url, headers):
        if url.endswith("/v2/account"):
            return 200, {
                "id": "upstream",
                "currency": "USD",
                "cash": "1000",
                "buying_power": "1000",
                "status": "ACTIVE",
                "trading_blocked": False,
                "account_blocked": False,
            }
        if "/v2/orders/" in url or "/v2/orders:by_client_order_id?" in url:
            return (
                self.order_response if self.order_response is not None else (404, None)
            )
        if url.endswith("/v2/positions"):
            return 200, []
        if "/v2/assets?" in url:
            return 200, [{"symbol": "VTI", "name": "Vanguard", "exchange": "ARCA"}]
        if url.endswith("/v2/assets/VTI"):
            return 200, {
                "symbol": "VTI",
                "status": "active",
                "class": "us_equity",
                "tradable": True,
            }
        if "/quotes/latest?feed=iex" in url:
            return 200, {
                "symbol": "VTI",
                "quote": {"t": NOW.isoformat(), "bp": "99", "ap": "100"},
            }
        if "/snapshot?feed=delayed_sip" in url:
            return 200, {
                "symbol": "VTI",
                "latestQuote": {"t": "2026-11-10T15:45:00Z", "bp": "99", "ap": "100"},
            }
        return 404, None


class FakeWrite:
    def __init__(self):
        self.requests = []

    def request(self, method, path, body=None):
        self.requests.append((method, path, body))
        if path == "/v2/account":
            return 200, {
                "id": "upstream",
                "status": "ACTIVE",
                "trading_blocked": False,
                "account_blocked": False,
            }
        if method == "POST":
            assert body is not None
            return 200, {
                "id": "broker-id",
                "client_order_id": body["client_order_id"],
                "symbol": "VTI",
                "status": "new",
            }
        if method == "GET":
            return 200, {"id": "broker-id", "status": "new"}
        if method == "DELETE":
            return 204, None
        return 404, None


def test_alpaca_order_and_cancel_use_shared_guards(tmp_path):
    settings = AlpacaMarketDataSettings("synthetic", "synthetic")
    http = FakeHttp()
    write = FakeWrite()

    class FakeAlpaca(AlpacaPortfolioProvider):
        def execution_provider(self, account_id, upstream_account_id, **kwargs):
            return AlpacaExecutionProvider(
                account_id, upstream_account_id, write, **kwargs
            )

    source = FakeAlpaca(settings, http_client=http, clock=lambda: NOW)
    market = AlpacaMarketDataProvider(settings, http_client=http, clock=lambda: NOW)
    app = create_app(
        source,
        configured_providers={"alpaca": source},
        market_data_provider=market,
        database_url=f"sqlite:///{tmp_path / 'portfolio.sqlite'}",
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        assert client.post("/api/connections/alpaca").status_code == 200
        assert client.post("/api/refresh").status_code == 200
        account = "alpaca:upstream"
        assert (
            client.put(
                f"/api/accounts/{account}/connection-settings",
                json={"trade_enabled": True, "version": 0},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/trading/settings",
                json={
                    "live_trading_enabled": True,
                    "kill_switch_active": False,
                    "max_order_shares": "5",
                    "max_order_notional_usd": "1000",
                    "version": 0,
                },
            ).status_code
            == 200
        )
        response = client.post(
            "/api/order-drafts",
            json={
                "account_id": account,
                "instrument_id": "us-equity:VTI",
                "side": "buy",
                "order_type": "market",
                "quantity": "1",
                "limit_price": None,
            },
        )
        assert response.status_code == 200, response.text
        draft = response.json()["draft"]
        assert draft["quote"]["source"] == "alpaca_iex_single_exchange"
        payload = {"expected_fingerprint": draft["fingerprint"], "confirmed": True}
        first = client.post(f"/api/order-drafts/{draft['id']}/confirm", json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["order"]["state"] == "ACCEPTED"
        assert (
            client.post(
                f"/api/order-drafts/{draft['id']}/confirm", json=payload
            ).status_code
            == 200
        )
        assert len([x for x in write.requests if x[0] == "POST"]) == 1
        command = next(x[2] for x in write.requests if x[0] == "POST")
        http.order_response = (
            200,
            {
                "id": "broker-id",
                "client_order_id": command["client_order_id"],
                "symbol": "VTI",
                "side": "buy",
                "type": "market",
                "qty": "1",
                "filled_qty": "1",
                "filled_avg_price": "100",
                "limit_price": None,
                "time_in_force": "day",
                "status": "filled",
                "submitted_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            },
        )
        order_id = first.json()["order"]["id"]
        refreshed = client.post(f"/api/orders/{order_id}/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["order"]["state"] == "FILLED"
        assert (
            client.post(f"/api/orders/{order_id}/reconcile").json()["order"]["state"]
            == "FILLED"
        )
        http.order_response = None
        cancel = client.post(
            f"/api/accounts/{account}/broker-orders/broker-id/cancel",
            json={"confirmed": True},
        )
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["state"] == "CANCEL_PENDING"
        assert (
            client.post(
                f"/api/accounts/{account}/broker-orders/broker-id/cancel",
                json={"confirmed": True},
            ).json()["state"]
            == "CANCEL_PENDING"
        )
        assert len([x for x in write.requests if x[0] == "DELETE"]) == 1


def test_unknown_submission_survives_restart_without_retry(tmp_path):
    settings = AlpacaMarketDataSettings("synthetic", "synthetic")
    http = FakeHttp()

    class TimeoutWrite(FakeWrite):
        def request(self, method, path, body=None):
            if method == "POST":
                self.requests.append((method, path, body))
                raise TimeoutError()
            return super().request(method, path, body)

    write = TimeoutWrite()

    class FakeAlpaca(AlpacaPortfolioProvider):
        def execution_provider(self, account_id, upstream_account_id, **kwargs):
            return AlpacaExecutionProvider(
                account_id, upstream_account_id, write, **kwargs
            )

    source = FakeAlpaca(settings, http_client=http, clock=lambda: NOW)
    market = AlpacaMarketDataProvider(settings, http_client=http, clock=lambda: NOW)
    url = f"sqlite:///{tmp_path / 'portfolio.sqlite'}"

    def app():
        return create_app(
            source,
            configured_providers={"alpaca": source},
            market_data_provider=market,
            database_url=url,
            clock=lambda: NOW,
        )

    with TestClient(app()) as client:
        assert client.post("/api/connections/alpaca").status_code == 200
        assert client.post("/api/refresh").status_code == 200
        account = "alpaca:upstream"
        assert (
            client.put(
                f"/api/accounts/{account}/connection-settings",
                json={"trade_enabled": True, "version": 0},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/trading/settings",
                json={
                    "live_trading_enabled": True,
                    "kill_switch_active": False,
                    "max_order_shares": "5",
                    "max_order_notional_usd": "1000",
                    "version": 0,
                },
            ).status_code
            == 200
        )
        response = client.post(
            "/api/order-drafts",
            json={
                "account_id": account,
                "instrument_id": "us-equity:VTI",
                "side": "buy",
                "order_type": "market",
                "quantity": "1",
                "limit_price": None,
            },
        )
        draft = response.json()["draft"]
        payload = {"expected_fingerprint": draft["fingerprint"], "confirmed": True}
        result = client.post(
            f"/api/order-drafts/{draft['id']}/confirm", json=payload
        ).json()["order"]
        assert result["state"] == "UNKNOWN"
        assert (
            client.get(f"/api/accounts/{account}/unresolved-orders").json()["orders"][
                0
            ]["id"]
            == result["id"]
        )
        assert client.get("/api/accounts/other/unresolved-orders").status_code == 404
    with TestClient(app()) as client:
        assert (
            client.get(f"/api/accounts/{account}/unresolved-orders").json()["orders"][
                0
            ]["id"]
            == result["id"]
        )
        replay = client.post(f"/api/order-drafts/{draft['id']}/confirm", json=payload)
        assert replay.json()["order"]["state"] == "UNKNOWN"
        assert (
            client.post(f"/api/orders/{result['id']}/reconcile").json()["order"][
                "state"
            ]
            == "UNKNOWN"
        )
        assert len([x for x in write.requests if x[0] == "POST"]) == 1


def test_dispatch_expiry_is_rejected_without_account_lockout(tmp_path):
    from datetime import timedelta

    settings = AlpacaMarketDataSettings("synthetic", "synthetic")
    http = FakeHttp()
    clock = [NOW]

    class SlowAccountWrite(FakeWrite):
        def __init__(self):
            super().__init__()
            self.account_reads = 0

        def request(self, method, path, body=None):
            if path == "/v2/account":
                self.account_reads += 1
                if self.account_reads == 2:
                    clock[0] += timedelta(seconds=61)
            return super().request(method, path, body)

    write = SlowAccountWrite()

    class FakeAlpaca(AlpacaPortfolioProvider):
        def execution_provider(self, account_id, upstream_account_id, **kwargs):
            return AlpacaExecutionProvider(
                account_id, upstream_account_id, write, **kwargs
            )

    source = FakeAlpaca(settings, http_client=http, clock=lambda: clock[0])
    market = AlpacaMarketDataProvider(
        settings, http_client=http, clock=lambda: clock[0]
    )
    app = create_app(
        source,
        configured_providers={"alpaca": source},
        market_data_provider=market,
        database_url=f"sqlite:///{tmp_path / 'portfolio.sqlite'}",
        clock=lambda: clock[0],
    )
    with TestClient(app) as client:
        assert client.post("/api/connections/alpaca").status_code == 200
        assert client.post("/api/refresh").status_code == 200
        account = "alpaca:upstream"
        assert (
            client.put(
                f"/api/accounts/{account}/connection-settings",
                json={"trade_enabled": True, "version": 0},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/trading/settings",
                json={
                    "live_trading_enabled": True,
                    "kill_switch_active": False,
                    "max_order_shares": "5",
                    "max_order_notional_usd": "1000",
                    "version": 0,
                },
            ).status_code
            == 200
        )
        body = {
            "account_id": account,
            "instrument_id": "us-equity:VTI",
            "side": "buy",
            "order_type": "market",
            "quantity": "1",
            "limit_price": None,
        }
        draft = client.post("/api/order-drafts", json=body).json()["draft"]
        result = client.post(
            f"/api/order-drafts/{draft['id']}/confirm",
            json={"expected_fingerprint": draft["fingerprint"], "confirmed": True},
        )
        assert result.json()["order"]["state"] == "REJECTED"
        assert result.json()["order"]["result"]["code"] == "dispatch_refused"
        assert not [entry for entry in write.requests if entry[0] == "POST"]
        clock[0] = NOW
        assert client.post("/api/order-drafts", json=body).status_code == 200


@pytest.mark.asyncio
async def test_cancel_dispatch_and_disconnect_complete_with_concurrent_api_calls(
    tmp_path,
):
    import asyncio
    import threading
    import time

    from httpx import ASGITransport, AsyncClient

    entered = threading.Event()
    release = threading.Event()

    class BlockingWrite(FakeWrite):
        def request(self, method, path, body=None):
            if method == "DELETE":
                self.requests.append((method, path, body))
                entered.set()
                if not release.wait(8):
                    raise TimeoutError("synthetic dispatch timed out")
                return 204, None
            return super().request(method, path, body)

    write = BlockingWrite()

    class FakeAlpaca(AlpacaPortfolioProvider):
        def execution_provider(self, account_id, upstream_account_id, **kwargs):
            return AlpacaExecutionProvider(
                account_id, upstream_account_id, write, **kwargs
            )

    settings = AlpacaMarketDataSettings("synthetic", "synthetic")
    source = FakeAlpaca(settings, http_client=FakeHttp(), clock=lambda: NOW)
    app = create_app(
        source,
        configured_providers={"alpaca": source},
        market_data_provider=AlpacaMarketDataProvider(
            settings, http_client=FakeHttp(), clock=lambda: NOW
        ),
        database_url=f"sqlite:///{tmp_path / 'api.sqlite'}",
        clock=lambda: NOW,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        assert (await client.post("/api/connections/alpaca")).status_code == 200
        assert (await client.post("/api/refresh")).status_code == 200
        assert (
            await client.put(
                "/api/accounts/alpaca:upstream/connection-settings",
                json={"trade_enabled": True, "version": 0},
            )
        ).status_code == 200
        assert (await client.get("/api/trading/settings")).json()["settings"][
            "kill_switch_active"
        ] is True
        cancel = asyncio.create_task(
            client.post(
                "/api/accounts/alpaca:upstream/broker-orders/broker-id/cancel",
                json={"confirmed": True},
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        timer = threading.Timer(3, release.set)
        timer.start()
        try:
            started = time.monotonic()
            disconnect = await client.post("/api/connections/alpaca/disconnect")
            assert time.monotonic() - started < 1
            assert disconnect.status_code == 409
            settings_response = await asyncio.wait_for(
                client.put(
                    "/api/trading/settings",
                    json={
                        "live_trading_enabled": False,
                        "kill_switch_active": True,
                        "max_order_shares": "1",
                        "max_order_notional_usd": "100",
                        "version": 0,
                    },
                ),
                timeout=1,
            )
            assert settings_response.status_code == 200
            release.set()
            response = await asyncio.wait_for(cancel, timeout=5)
            assert (
                await client.post("/api/connections/alpaca/disconnect")
            ).status_code == 200
        finally:
            release.set()
            timer.cancel()
        assert response.status_code == 200
        assert response.json()["state"] == "CANCEL_PENDING"
        assert [method for method, _, _ in write.requests].count("DELETE") == 1
