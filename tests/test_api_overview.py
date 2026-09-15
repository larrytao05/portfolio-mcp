from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.models import Account, HoldingsSnapshot, Position
from portfolio_mcp.provider import ProviderUnavailableError


class NonUsdPortfolioProvider(FixturePortfolioProvider):
    def __init__(self) -> None:
        super().__init__()
        self._positions["schwab-taxable-demo"].append(
            Position(
                account_id="schwab-taxable-demo",
                symbol="SHOP.TO",
                name="Shopify Inc.",
                asset_class="equity",
                quantity=Decimal("10"),
                current_price=Decimal("100.00"),
                market_value=Decimal("1000.00"),
                cost_basis=Decimal("900.00"),
                currency="CAD",
            )
        )


class PartiallyFailingFixtureProvider(FixturePortfolioProvider):
    async def get_holdings(self, account_id: str) -> HoldingsSnapshot:
        if account_id == "schwab-taxable-demo":
            raise ProviderUnavailableError("Schwab fixture data is unavailable")
        return await super().get_holdings(account_id)


def create_client(
    provider: FixturePortfolioProvider | None = None,
    tmp_path: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[TestClient, PortfolioRepository]:
    assert tmp_path is not None
    if provider is None:
        provider = FixturePortfolioProvider()
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    app = create_app(
        provider,
        database_url=database_url,
        clock=clock,
    )
    return TestClient(app), PortfolioRepository(database_url)


def test_fresh_overview_endpoint(tmp_path) -> None:
    def fixed_clock() -> datetime:
        return datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)

    client, _ = create_client(tmp_path=tmp_path, clock=fixed_clock)

    refresh_resp = client.post("/api/refresh")
    assert refresh_resp.status_code == 200

    response = client.get("/api/overview")
    assert response.status_code == 200
    overview = response.json()["overview"]

    assert overview["status"] == "fresh"
    assert overview["total_known_usd_value"] == "9999.95"
    assert overview["as_of"] == "2026-08-29"
    assert overview["refreshed_at"] is not None
    assert overview["refreshed_at"].endswith("+00:00")
    assert overview["warnings"] == []
    assert overview["exclusions"] == []

    # Accounts
    accounts = overview["accounts"]
    assert len(accounts) == 2
    for acc in accounts:
        assert isinstance(acc["account_id"], str)
        assert isinstance(acc["label"], str)
        assert isinstance(acc["provider"], str)
        assert isinstance(acc["account_type"], str)
        assert isinstance(acc["currency"], str)
        assert isinstance(acc["market_value"], str)
        assert acc["is_stale"] is False
        assert isinstance(acc["percentage_of_total"], str)

    # Allocations
    allocations = overview["allocations"]
    assert "account" in allocations
    assert "asset_class" in allocations

    account_alloc = allocations["account"]
    assert account_alloc["group_by"] == "account"
    assert account_alloc["denominator"] == "9999.95"
    assert account_alloc["included_count"] == 9
    assert account_alloc["excluded_count"] == 0
    assert len(account_alloc["slices"]) == 2
    for s in account_alloc["slices"]:
        assert isinstance(s["amount"], str)
        assert isinstance(s["percentage"], str)
        assert isinstance(s["position_count"], int)

    asset_alloc = allocations["asset_class"]
    assert asset_alloc["group_by"] == "asset_class"
    assert asset_alloc["denominator"] == "9999.95"
    assert asset_alloc["included_count"] == 9
    assert asset_alloc["excluded_count"] == 0
    assert len(asset_alloc["slices"]) > 0

    # Gain / loss
    gain_loss = overview["gain_loss"]
    assert gain_loss["unrealized_gain_loss"] == "758.95"
    assert gain_loss["cost_basis"] == "9241.00"
    assert gain_loss["market_value"] == "9999.95"
    assert gain_loss["included_count"] == 9
    assert gain_loss["excluded_count"] == 0

    # History in overview
    history = overview["history"]
    assert len(history) == 1
    assert history[0]["date"] == "2026-08-29"
    assert history[0]["value"] == "9999.95"
    assert history[0]["currency"] == "USD"
    assert history[0]["accounts_count"] == 2


def test_empty_portfolio_overview(tmp_path) -> None:
    client, _ = create_client(tmp_path=tmp_path)

    response = client.get("/api/overview")
    assert response.status_code == 200
    overview = response.json()["overview"]

    assert overview["status"] == "empty"
    assert overview["total_known_usd_value"] is None
    assert overview["as_of"] is None
    assert overview["refreshed_at"] is None
    assert overview["accounts"] == []
    assert overview["exclusions"] == []
    assert overview["warnings"] == []
    assert overview["history"] == []

    assert overview["allocations"]["account"]["slices"] == []
    assert overview["allocations"]["account"]["denominator"] == "0"
    assert overview["allocations"]["account"]["included_count"] == 0
    assert overview["allocations"]["account"]["excluded_count"] == 0

    assert overview["allocations"]["asset_class"]["slices"] == []
    assert overview["allocations"]["asset_class"]["denominator"] == "0"
    assert overview["allocations"]["asset_class"]["included_count"] == 0
    assert overview["allocations"]["asset_class"]["excluded_count"] == 0

    assert overview["gain_loss"] == {
        "unrealized_gain_loss": None,
        "cost_basis": None,
        "market_value": None,
        "included_count": 0,
        "excluded_count": 0,
    }


def test_partial_stale_refresh_overview(tmp_path) -> None:
    client, _ = create_client(tmp_path=tmp_path)
    client.post("/api/refresh")

    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    partial_app = create_app(
        PartiallyFailingFixtureProvider(),
        database_url=database_url,
    )
    partial_client = TestClient(partial_app)
    refresh_resp = partial_client.post("/api/refresh")
    assert refresh_resp.status_code == 200

    overview_resp = partial_client.get("/api/overview")
    assert overview_resp.status_code == 200
    overview = overview_resp.json()["overview"]

    assert overview["status"] == "partial"
    assert len(overview["warnings"]) > 0
    assert any("Schwab" in w for w in overview["warnings"])

    accounts = {a["label"]: a for a in overview["accounts"]}
    assert accounts["Schwab Taxable ••••4821"]["is_stale"] is True
    assert accounts["Fidelity Roth IRA ••••9046"]["is_stale"] is False


def test_non_usd_holdings_appear_in_exclusions(tmp_path) -> None:
    client, _ = create_client(provider=NonUsdPortfolioProvider(), tmp_path=tmp_path)

    client.post("/api/refresh")

    response = client.get("/api/overview")
    assert response.status_code == 200
    overview = response.json()["overview"]

    assert overview["total_known_usd_value"] == "9999.95"

    exclusions = overview["exclusions"]
    assert len(exclusions) >= 1
    cad_exclusion = next(e for e in exclusions if e["symbol"] == "SHOP.TO")
    assert cad_exclusion["reason"] == "unsupported_currency"
    assert "CAD" in cad_exclusion["details"]

    assert overview["allocations"]["account"]["excluded_count"] == 1
    assert overview["allocations"]["asset_class"]["excluded_count"] == 1
    assert overview["gain_loss"]["excluded_count"] == 1


def test_history_endpoint_preserves_gaps(tmp_path) -> None:
    client, repo = create_client(tmp_path=tmp_path)

    account_1 = Account(
        id="acc-1", provider="P1", label="Acc 1", account_type="taxable", currency="USD"
    )
    account_2 = Account(
        id="acc-2", provider="P2", label="Acc 2", account_type="taxable", currency="USD"
    )

    now = datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC)
    snap1 = HoldingsSnapshot(
        account=account_1,
        as_of=date(2026, 8, 20),
        positions=(
            Position(
                account_id="acc-1",
                symbol="VTI",
                name="Vanguard",
                asset_class="etf",
                quantity=Decimal("10"),
                current_price=Decimal("100"),
                market_value=Decimal("1000.00"),
                cost_basis=Decimal("900.00"),
                currency="USD",
            ),
        ),
    )
    repo.save_refresh([snap1], now, now, snapshot_date=date(2026, 8, 20))

    now2 = datetime(2026, 8, 25, 10, 0, 0, tzinfo=UTC)
    snap2_1 = HoldingsSnapshot(
        account=account_1,
        as_of=date(2026, 8, 25),
        positions=(
            Position(
                account_id="acc-1",
                symbol="VTI",
                name="Vanguard",
                asset_class="etf",
                quantity=Decimal("10"),
                current_price=Decimal("105"),
                market_value=Decimal("1050.00"),
                cost_basis=Decimal("900.00"),
                currency="USD",
            ),
        ),
    )
    snap2_2 = HoldingsSnapshot(
        account=account_2,
        as_of=date(2026, 8, 25),
        positions=(
            Position(
                account_id="acc-2",
                symbol="BND",
                name="Bond",
                asset_class="bond",
                quantity=Decimal("10"),
                current_price=Decimal("50"),
                market_value=Decimal("500.00"),
                cost_basis=Decimal("500.00"),
                currency="USD",
            ),
        ),
    )
    repo.save_refresh([snap2_1, snap2_2], now2, now2, snapshot_date=date(2026, 8, 25))

    response = client.get("/api/overview/history")
    assert response.status_code == 200
    history = response.json()["history"]

    assert len(history) == 2
    assert history[0] == {
        "date": "2026-08-20",
        "value": "1000.00",
        "currency": "USD",
        "accounts_count": 1,
    }
    assert history[1] == {
        "date": "2026-08-25",
        "value": "1550.00",
        "currency": "USD",
        "accounts_count": 2,
    }


class TrackingPortfolioProvider(FixturePortfolioProvider):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def list_accounts(self) -> list[Account]:
        self.call_count += 1
        return await super().list_accounts()

    async def get_holdings(self, account_id: str) -> HoldingsSnapshot:
        self.call_count += 1
        return await super().get_holdings(account_id)


def test_overview_endpoints_make_zero_provider_calls(tmp_path) -> None:
    provider = TrackingPortfolioProvider()
    client, _ = create_client(provider=provider, tmp_path=tmp_path)

    # Refresh will make provider calls
    client.post("/api/refresh")
    initial_calls = provider.call_count
    assert initial_calls > 0

    # Calling /api/overview must make ZERO provider calls
    resp = client.get("/api/overview")
    assert resp.status_code == 200
    assert provider.call_count == initial_calls

    # Calling /api/overview/history must make ZERO provider calls
    resp_hist = client.get("/api/overview/history")
    assert resp_hist.status_code == 200
    assert provider.call_count == initial_calls


def test_missing_market_value_and_cost_basis_exclusions(tmp_path) -> None:
    client, repo = create_client(tmp_path=tmp_path)

    account = Account(
        id="acc-test",
        provider="P",
        label="Acc ••••1111",
        account_type="taxable",
        currency="USD",
    )
    now = datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC)
    snap = HoldingsSnapshot(
        account=account,
        as_of=date(2026, 8, 20),
        positions=(
            Position(
                account_id="acc-test",
                symbol="UNVALUED",
                name="Unvalued Corp",
                asset_class="equity",
                quantity=Decimal("10"),
                current_price=None,
                market_value=None,
                cost_basis=Decimal("100.00"),
                currency="USD",
            ),
            Position(
                account_id="acc-test",
                symbol="NOBASIS",
                name="No Basis Corp",
                asset_class="equity",
                quantity=Decimal("5"),
                current_price=Decimal("50.00"),
                market_value=Decimal("250.00"),
                cost_basis=None,
                currency="USD",
            ),
        ),
    )
    repo.save_refresh([snap], now, now, snapshot_date=date(2026, 8, 20))

    resp = client.get("/api/overview")
    assert resp.status_code == 200
    overview = resp.json()["overview"]

    # Total should only include NOBASIS (250.00)
    assert overview["total_known_usd_value"] == "250.00"

    reasons = {e["symbol"]: e["reason"] for e in overview["exclusions"]}
    assert reasons["UNVALUED"] == "missing_market_value"
    assert reasons["NOBASIS"] == "missing_cost_basis"

    # Gain loss should have 0 included because neither has both market_value
    # and cost_basis
    assert overview["gain_loss"]["included_count"] == 0
    assert overview["gain_loss"]["excluded_count"] == 2
    assert overview["gain_loss"]["unrealized_gain_loss"] is None
