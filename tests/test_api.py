from datetime import UTC, datetime

from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.fixtures import FixturePortfolioProvider


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
    assert len(values) == 1
    assert str(values[0].value) == "4799.97000000"
    assert values[0].recorded_at == datetime(2026, 9, 12, 20, 1, tzinfo=UTC)
