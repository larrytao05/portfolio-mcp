from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.fixtures import FixturePortfolioProvider


def test_health() -> None:
    client = TestClient(create_app(FixturePortfolioProvider()))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_accounts() -> None:
    client = TestClient(create_app(FixturePortfolioProvider()))

    response = client.get("/api/accounts")

    assert response.status_code == 200
    assert response.json()["accounts"][0]["provider"] == "Schwab"
