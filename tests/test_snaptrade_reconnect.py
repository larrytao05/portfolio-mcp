from datetime import date

from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.snaptrade import SnapTradeProvider


class FakeSnapTrade(SnapTradeProvider):
    def __init__(self) -> None:
        self.fixture = FixturePortfolioProvider()
        self.enabled = True
        self.mapped = True

    async def connection_accounts(self):
        ids = ["schwab-taxable-demo"] if self.mapped else []
        return [("fictional-auth", self.enabled, ids)]

    async def list_accounts(self):
        return [
            account
            for account in await self.fixture.list_accounts()
            if account.id == "schwab-taxable-demo"
        ]

    async def get_holdings(self, account_id):
        return await self.fixture.get_holdings(account_id)

    async def get_transactions(self, account_id, start_date: date, end_date: date):
        return await self.fixture.get_transactions(account_id, start_date, end_date)

    async def get_broker_orders(self, account_id, symbol):
        return await self.fixture.get_broker_orders(account_id, symbol)


def test_reconnect_requires_verified_authorization_and_explicit_action(tmp_path):
    source = FakeSnapTrade()
    app = create_app(
        source,
        configured_providers={"snaptrade": source},
        database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
    )
    with TestClient(app) as client:
        assert client.post("/api/connections/snaptrade").status_code == 200
        assert client.post("/api/refresh").json()["refresh"]["status"] == "success"
        account = client.get("/api/connections").json()["accounts"][0]
        assert (
            client.put(
                f"/api/accounts/{account['account_id']}/connection-settings",
                json={"version": account["version"], "trade_enabled": True},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/connections/snaptrade:fictional-auth/disconnect"
            ).status_code
            == 200
        )
        assert client.post("/api/connections/snaptrade").status_code == 200
        assert not client.get("/api/connections").json()["accounts"][0]["active"]
        source.enabled = False
        assert (
            client.post(
                "/api/connections/snaptrade:fictional-auth/reactivate"
            ).status_code
            == 409
        )
        source.enabled = True
        source.mapped = False
        assert (
            client.post(
                "/api/connections/snaptrade:fictional-auth/reactivate"
            ).status_code
            == 409
        )
        source.mapped = True
        assert (
            client.post(
                "/api/connections/snaptrade:fictional-auth/reactivate"
            ).status_code
            == 200
        )
        account = client.get("/api/connections").json()["accounts"][0]
        assert account["active"] and not account["trade_enabled"]


def test_upstream_disabled_refresh_and_orders_fail_closed_preserving_history(tmp_path):
    source = FakeSnapTrade()
    app = create_app(
        source,
        configured_providers={"snaptrade": source},
        database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
    )
    with TestClient(app) as client:
        assert client.post("/api/connections/snaptrade").status_code == 200
        assert client.post("/api/refresh").json()["refresh"]["status"] == "success"
        before = client.get("/api/accounts/schwab-taxable-demo/positions").json()
        source.enabled = False
        refresh = client.post("/api/refresh").json()["refresh"]
        assert refresh["status"] == "failed"
        assert client.get("/api/accounts/schwab-taxable-demo/positions").json()[
            "positions"
        ]
        assert len(
            client.get("/api/accounts/schwab-taxable-demo/positions").json()[
                "positions"
            ]
        ) == len(before["positions"])
        assert (
            client.get(
                "/api/accounts/schwab-taxable-demo/broker-orders?symbol=VTI"
            ).status_code
            == 503
        )
        assert client.post("/api/connections/snaptrade").status_code == 200
        assert not client.get("/api/connections").json()["accounts"][0]["active"]
