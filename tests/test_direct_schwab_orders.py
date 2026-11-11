from datetime import UTC, datetime

import pytest

from portfolio_mcp.config import SchwabMarketDataSettings
from portfolio_mcp.direct_schwab_orders import DirectSchwabOrders
from portfolio_mcp.models import Account
from portfolio_mcp.schwab_orders import SchwabOrderLeg, SchwabOrderResult


@pytest.mark.asyncio
async def test_direct_orders_use_explicit_binding_and_preserve_leg_ownership(
    monkeypatch,
):
    calls = []

    class FakeReader:
        def __init__(self, account_id, encrypted_account_id, token):
            calls.append((account_id, encrypted_account_id, token))

        async def list_orders(self, start, end):
            assert (end - start).days == 90
            legs = tuple(
                SchwabOrderLeg(
                    "canonical",
                    "42",
                    str(leg),
                    "VTI",
                    "BUY",
                    "LIMIT",
                    "1",
                    None,
                    None,
                    "WORKING",
                    None,
                    None,
                )
                for leg in (1, 2)
            )
            return SchwabOrderResult(
                legs,
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 4, 1, tzinfo=UTC),
                True,
            )

    async def fake_token():
        return "synthetic-token"

    monkeypatch.setattr(
        "portfolio_mcp.direct_schwab_orders.SchwabOrderReader", FakeReader
    )
    service = DirectSchwabOrders(
        SchwabMarketDataSettings("id", "secret", "refresh"),
        {"canonical": "explicit-hash"},
    )
    monkeypatch.setattr(service._auth, "_access_token", fake_token)
    assert not service.has_account("another-account")
    result = await service.get_orders(
        Account("canonical", "Schwab", "A", "brokerage", "USD"), "VTI", "open"
    )
    assert calls == [("canonical", "explicit-hash", "synthetic-token")]
    assert result.truncated
    assert [row.leg_id for row in result.orders] == ["1", "2"]
    assert all(row.account_id == "canonical" for row in result.orders)
    assert not (await service.get_orders(result.account, "VTI", "closed")).orders


def test_direct_schwab_api_refreshes_oauth_and_uses_verified_account_route(
    monkeypatch, tmp_path
):
    import asyncio
    from dataclasses import replace
    from urllib.parse import parse_qs, urlsplit

    from fastapi.testclient import TestClient

    from portfolio_mcp.api import create_app
    from portfolio_mcp.database import PortfolioRepository
    from portfolio_mcp.fixtures import FixturePortfolioProvider
    from portfolio_mcp.snaptrade import SnapTradeProvider

    url = f"sqlite:///{tmp_path / 'portfolio.sqlite'}"
    repository = PortfolioRepository(url)
    repository.save_connection(
        "snaptrade:auth-1", "snaptrade", "live", "server_env:snaptrade", True
    )
    repository.save_account_route("canonical", "snaptrade:auth-1", "upstream")
    fixture = FixturePortfolioProvider()
    snapshot = asyncio.run(fixture.get_holdings("schwab-taxable-demo"))
    snapshot = replace(
        snapshot,
        account=replace(snapshot.account, id="canonical"),
        positions=tuple(replace(p, account_id="canonical") for p in snapshot.positions),
    )
    now = datetime.now(UTC)
    repository.save_refresh([snapshot], now, now, now.date())
    snaptrade = SnapTradeProvider.__new__(SnapTradeProvider)

    async def connection_accounts():
        return [("auth-1", True, ["upstream"])]

    monkeypatch.setattr(snaptrade, "connection_accounts", connection_accounts)
    requests = []

    def oauth_request(self, method, url, headers, body=None):
        requests.append((method, url))
        assert method == "POST" and url == "https://api.schwabapi.com/v1/oauth/token"
        assert body is not None
        assert parse_qs(body.decode())["grant_type"] == ["refresh_token"]
        return 200, {"access_token": "synthetic-token"}

    def order_request(self, url, headers):
        requests.append(("GET", url))
        assert headers["Authorization"] == "Bearer synthetic-token"
        assert urlsplit(url).path == "/trader/v1/accounts/explicit-hash/orders"
        query = parse_qs(urlsplit(url).query)
        assert query["maxResults"] == ["3000"]
        assert "fromEnteredTime" in query and "toEnteredTime" in query
        return 200, [
            {
                "orderId": 9007199254740993,
                "status": "WORKING",
                "orderType": "LIMIT",
                "price": "12.30",
                "filledQuantity": "0.5",
                "orderLegCollection": [
                    {
                        "legId": 7,
                        "instrument": {"symbol": "VTI"},
                        "instruction": "BUY",
                        "quantity": "2",
                    }
                ],
            }
        ]

    monkeypatch.setattr(
        "portfolio_mcp.schwab_market_data.UrllibSchwabHttpClient.request", oauth_request
    )
    monkeypatch.setattr(
        "portfolio_mcp.alpaca_market_data.UrllibAlpacaHttpClient.request", order_request
    )
    direct = DirectSchwabOrders(
        SchwabMarketDataSettings("id", "secret", "refresh"),
        {"canonical": "explicit-hash"},
    )
    app = create_app(
        snaptrade,
        configured_providers={"snaptrade": snaptrade},
        database_url=url,
        direct_schwab_orders=direct,
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/accounts/canonical/broker-orders",
            params={"symbol": "VTI", "status": "open"},
        )
        assert response.status_code == 200
        history = response.json()["history"]
        assert history["account"]["id"] == "canonical"
        assert history["source"] == "Schwab direct"
        assert history["orders"][0]["id"] == "9007199254740993"
        assert history["orders"][0]["account_id"] == "canonical"
        assert history["orders"][0]["leg_id"] == "7"
        assert len(requests) == 2
        repository.disconnect_connection("snaptrade:auth-1")
        rejected = client.get(
            "/api/accounts/canonical/broker-orders",
            params={"symbol": "VTI", "status": "open"},
        )
        assert rejected.status_code == 502
        assert len(requests) == 2
