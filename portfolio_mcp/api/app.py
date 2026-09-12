from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from portfolio_mcp.provider import PortfolioProvider


def create_app(provider: PortfolioProvider) -> FastAPI:
    app = FastAPI(title="Portfolio Dashboard API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET"],
        allow_headers=[],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/accounts")
    async def list_accounts() -> dict[str, list[dict[str, str]]]:
        accounts = await provider.list_accounts()
        return {"accounts": [account.to_dict() for account in accounts]}

    return app
