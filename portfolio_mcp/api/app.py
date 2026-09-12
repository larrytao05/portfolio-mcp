from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.provider import PortfolioProvider, ProviderError
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

    @app.get("/api/accounts/{account_id}/positions")
    async def list_positions(account_id: str) -> dict[str, object]:
        positions = repository.list_positions(account_id)
        if positions is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"positions": [position.to_dict() for position in positions]}

    @app.get("/api/accounts/{account_id}")
    async def get_account(account_id: str) -> dict[str, object]:
        account = repository.account_detail(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"account": account.to_dict()}

    @app.get("/api/accounts/{account_id}/daily-values")
    async def list_daily_values(account_id: str) -> dict[str, object]:
        values = repository.daily_values(account_id)
        if values is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"daily_values": [value.to_dict() for value in values]}

    @app.get("/api/refreshes/latest")
    async def latest_refresh() -> dict[str, object]:
        result = repository.latest_refresh()
        return {"refresh": result.to_dict() if result is not None else None}

    @app.post("/api/refresh")
    async def refresh_portfolio() -> dict[str, dict[str, int | str | None]]:
        result = await refresh_service.refresh()
        return {"refresh": result.to_dict()}

    @app.exception_handler(ProviderError)
    async def provider_error(_: Request, error: ProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "provider_error", "message": str(error)}},
        )

    return app
