from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.provider import PortfolioProvider
from portfolio_mcp.refresh import PortfolioRefreshService


def create_app(
    provider: PortfolioProvider,
    *,
    database_url: str = "sqlite:///portfolio.db",
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    repository = PortfolioRepository(database_url)
    refresh_service = PortfolioRefreshService(provider, repository, clock)
    app = FastAPI(title="Portfolio Dashboard API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=[],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/accounts")
    async def list_accounts() -> dict[str, list[dict[str, str]]]:
        accounts = repository.list_accounts()
        return {"accounts": [account.to_dict() for account in accounts]}

    @app.post("/api/refresh")
    async def refresh_portfolio() -> dict[str, dict[str, int | str]]:
        result = await refresh_service.refresh()
        return {"refresh": result.to_dict()}

    return app
