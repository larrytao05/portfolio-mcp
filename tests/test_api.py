from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.models import Account, HoldingsSnapshot, Position
from portfolio_mcp.provider import ProviderUnavailableError


class FailingFixtureProvider(FixturePortfolioProvider):
    async def list_accounts(self) -> list[Account]:
        raise ProviderUnavailableError("Fixture provider is unavailable")


def create_client(tmp_path, clock=None) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    return TestClient(
        create_app(
            FixturePortfolioProvider(),
            database_url=database_url,
            clock=clock,
        )
    )


def test_health(tmp_path) -> None:
    client = create_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_refresh_persists_accounts(tmp_path) -> None:
    client = create_client(tmp_path)

    response = client.post("/api/refresh")

    assert response.status_code == 200
    assert response.json()["refresh"] == {
        "id": 1,
        "status": "success",
        "started_at": response.json()["refresh"]["started_at"],
        "completed_at": response.json()["refresh"]["completed_at"],
        "accounts_refreshed": 2,
        "positions_refreshed": 9,
        "daily_snapshots_recorded": 2,
        "error_code": None,
        "error_message": None,
    }
    assert response.json()["refresh"]["started_at"].endswith("+00:00")

    response = client.get("/api/accounts")

    assert response.status_code == 200
    assert {account["provider"] for account in response.json()["accounts"]} == {
        "Fidelity",
        "Schwab",
    }


def test_persisted_accounts_survive_an_app_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    first_client = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )
    first_client.post("/api/refresh")
    second_client = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )

    response = second_client.get("/api/accounts")

    assert len(response.json()["accounts"]) == 2
    positions = second_client.get("/api/accounts/schwab-taxable-demo/positions")
    assert len(positions.json()["positions"]) == 4
    assert positions.json()["positions"][0]["as_of"] == "2026-08-29"
    daily_values = second_client.get("/api/accounts/schwab-taxable-demo/daily-values")
    assert daily_values.json()["daily_values"][0]["value"] == "4799.97"
    latest_refresh = second_client.get("/api/refreshes/latest")
    assert latest_refresh.json()["refresh"]["status"] == "success"


def test_account_detail_uses_persisted_account_metadata_and_holdings(tmp_path) -> None:
    refreshed_at = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    client = create_client(tmp_path, clock=lambda: refreshed_at)
    client.post("/api/refresh")

    response = client.get("/api/accounts/schwab-taxable-demo")

    assert response.status_code == 200
    account = response.json()["account"]
    assert account["label"] == "Schwab Taxable ••••4821"
    assert account["provider"] == "Schwab"
    assert account["account_type"] == "taxable_brokerage"
    assert account["currency"] == "USD"
    assert account["refreshed_at"] == "2026-09-12T14:00:00+00:00"
    assert account["as_of"] == "2026-08-29"
    assert account["balances"] == {
        "market_value": "4799.97",
        "cost_basis": "4383.00",
        "currency": "USD",
    }
    assert account["positions"][0]["gain_loss"] == "60.00"


def test_account_detail_preserves_empty_and_unavailable_values(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    repository = PortfolioRepository(database_url)
    refreshed_at = datetime(2020, 1, 2, tzinfo=UTC)
    empty_account = Account(
        id="empty-account",
        provider="Fixture",
        label="Empty ••••0001",
        account_type="taxable_brokerage",
        currency="USD",
    )
    unavailable_account = Account(
        id="unavailable-account",
        provider="Fixture",
        label="Unavailable ••••0002",
        account_type="roth_ira",
        currency="USD",
    )
    repository.save_refresh(
        [
            HoldingsSnapshot(
                account=empty_account,
                as_of=date(2020, 1, 2),
                positions=(),
            ),
            HoldingsSnapshot(
                account=unavailable_account,
                as_of=date(2020, 1, 2),
                positions=(
                    Position(
                        account_id=unavailable_account.id,
                        symbol="UNKNOWN",
                        name="Unavailable values fund",
                        asset_class="fund",
                        quantity=Decimal("1"),
                        current_price=None,
                        market_value=None,
                        cost_basis=None,
                        currency="USD",
                    ),
                ),
            ),
        ],
        refreshed_at,
        refreshed_at,
        date(2020, 1, 2),
    )
    client = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )

    empty = client.get("/api/accounts/empty-account")
    unavailable = client.get("/api/accounts/unavailable-account")

    assert empty.status_code == 200
    assert empty.json()["account"]["positions"] == []
    assert empty.json()["account"]["balances"]["market_value"] == "0"
    assert unavailable.status_code == 200
    detail = unavailable.json()["account"]
    assert detail["refreshed_at"] == "2020-01-02T00:00:00+00:00"
    assert detail["balances"]["market_value"] is None
    assert detail["balances"]["cost_basis"] is None
    assert detail["positions"][0]["current_price"] is None
    assert detail["positions"][0]["cost_basis"] is None
    assert detail["positions"][0]["gain_loss"] is None


def test_refresh_replaces_the_same_new_york_daily_snapshot(tmp_path) -> None:
    times = iter(
        [
            datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
            datetime(2026, 9, 12, 4, 1, tzinfo=UTC),
            datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
            datetime(2026, 9, 12, 20, 1, tzinfo=UTC),
        ]
    )
    client = create_client(tmp_path, clock=lambda: next(times))

    client.post("/api/refresh")
    client.post("/api/refresh")

    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    values = repository.daily_values("schwab-taxable-demo")
    assert values is not None
    assert len(values) == 1
    assert values[0].value == Decimal("4799.97")
    assert values[0].recorded_at == datetime(2026, 9, 12, 20, 1, tzinfo=UTC)


def test_refresh_does_not_fabricate_missing_new_york_daily_snapshots(tmp_path) -> None:
    times = iter(
        [
            datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
            datetime(2026, 9, 12, 4, 1, tzinfo=UTC),
            datetime(2026, 9, 14, 4, 0, tzinfo=UTC),
            datetime(2026, 9, 14, 4, 1, tzinfo=UTC),
        ]
    )
    client = create_client(tmp_path, clock=lambda: next(times))

    client.post("/api/refresh")
    client.post("/api/refresh")

    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    values = repository.daily_values("schwab-taxable-demo")

    assert values is not None
    assert [value.snapshot_date for value in values] == [
        date(2026, 9, 12),
        date(2026, 9, 14),
    ]


def test_database_preserves_high_precision_decimals(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    repository = PortfolioRepository(database_url)
    timestamp = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    account = Account(
        id="precise-account",
        provider="Fixture",
        label="Fixture ••••1234",
        account_type="taxable_brokerage",
        currency="USD",
    )
    precise_quantity = Decimal("0.123456789123456789")
    repository.save_refresh(
        [
            HoldingsSnapshot(
                account=account,
                as_of=date(2026, 9, 12),
                positions=(
                    Position(
                        account_id=account.id,
                        symbol="VTI",
                        name="Vanguard Total Stock Market ETF",
                        asset_class="equity_etf",
                        quantity=precise_quantity,
                        current_price=Decimal("333.333333333333333333"),
                        market_value=Decimal("41.152263041152262999958847737"),
                        cost_basis=Decimal("40.000000000000000001"),
                        currency="USD",
                    ),
                ),
            )
        ],
        timestamp,
        timestamp,
        date(2026, 9, 12),
    )

    reopened_repository = PortfolioRepository(database_url)
    positions = reopened_repository.list_positions(account.id)

    assert positions is not None
    assert positions[0].position.quantity == precise_quantity
    assert positions[0].position.current_price == Decimal("333.333333333333333333")
    assert positions[0].position.market_value == Decimal(
        "41.152263041152262999958847737"
    )
    assert positions[0].position.cost_basis == Decimal("40.000000000000000001")


def test_failed_refresh_is_persisted_without_replacing_saved_data(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    successful_client = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )
    successful_client.post("/api/refresh")
    failing_client = TestClient(
        create_app(FailingFixtureProvider(), database_url=database_url)
    )

    response = failing_client.post("/api/refresh")

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "provider_error",
            "message": "Fixture provider is unavailable",
        }
    }
    assert len(failing_client.get("/api/accounts").json()["accounts"]) == 2
    latest = failing_client.get("/api/refreshes/latest").json()["refresh"]
    assert latest["status"] == "failed"
    assert latest["error_code"] == "provider_error"
    assert latest["error_message"] == "Fixture provider is unavailable"
