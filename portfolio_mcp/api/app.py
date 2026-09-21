from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.execution import ExecutionProvider, FixtureExecutionProvider
from portfolio_mcp.fixtures import FixtureMarketDataProvider
from portfolio_mcp.overview import OverviewService
from portfolio_mcp.provider import (
    InstrumentNotFoundError,
    MarketDataProvider,
    PortfolioProvider,
    ProviderError,
)
from portfolio_mcp.refresh import PortfolioRefreshService
from portfolio_mcp.trading_service import (
    OrderDraftService,
    OrderSubmissionService,
    SubmissionValidator,
    TradingValidationError,
    fixture_submission_validator,
)


class CreateOrderDraftRequest(BaseModel):
    account_id: str
    instrument_id: str
    side: str
    order_type: str
    quantity: str
    limit_price: str | None = None


class ConfirmOrderDraftRequest(BaseModel):
    expected_fingerprint: str
    confirmed: bool


def create_app(
    provider: PortfolioProvider,
    *,
    market_data_provider: MarketDataProvider | None = None,
    execution_provider: ExecutionProvider | None = None,
    submission_validator: SubmissionValidator | None = None,
    database_url: str = "sqlite:///portfolio.db",
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    repository = PortfolioRepository(database_url, clock)
    refresh_service = PortfolioRefreshService(provider, repository, clock)
    market_data = market_data_provider or FixtureMarketDataProvider()
    service_clock = clock or (lambda: datetime.now(UTC))
    execution = (
        execution_provider
        if execution_provider is not None
        else FixtureExecutionProvider()
    )
    validator = submission_validator
    if validator is None:
        if type(execution) is not FixtureExecutionProvider:
            raise ValueError("A final trading policy validator is required")
        validator = fixture_submission_validator(provider)
    draft_service = OrderDraftService(repository, provider, market_data, service_clock)
    submission_service = OrderSubmissionService(
        repository, execution, service_clock, validator
    )
    overview_service = OverviewService(repository)
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

    @app.get("/api/overview")
    async def get_overview() -> dict[str, object]:
        overview = overview_service.get_overview()
        return {"overview": overview.to_dict()}

    @app.get("/api/overview/history")
    async def get_overview_history() -> dict[str, object]:
        history = overview_service.get_history()
        return {"history": [point.to_dict() for point in history.points]}

    @app.get("/api/accounts")
    async def list_accounts() -> dict[str, list[dict[str, str | bool]]]:
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

    @app.get("/api/trading/status")
    async def trading_status() -> dict[str, object]:
        return {
            "providers": [health.to_dict() for health in repository.provider_health()],
            "accounts": [
                capability.to_dict() for capability in repository.current_capabilities()
            ],
        }

    @app.get("/api/accounts/{account_id}/capabilities")
    async def account_capabilities(account_id: str) -> dict[str, object]:
        capability = repository.account_capability(account_id)
        if capability is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "account_not_found", "message": "Account not found"},
            )
        return {"capability": capability.to_dict()}

    @app.get("/api/refreshes/latest")
    async def latest_refresh() -> dict[str, object]:
        result = repository.latest_refresh()
        return {"refresh": result.to_dict() if result is not None else None}

    @app.get("/api/activity")
    async def list_activity(
        account_id: str | None = None,
        provider: str | None = None,
        transaction_type: Annotated[str | None, Query(alias="type")] = None,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        if start_date is not None and end_date is not None and start_date > end_date:
            raise HTTPException(
                status_code=422, detail="start_date must be on or before end_date"
            )
        activities, total = repository.list_activities(
            account_id=account_id,
            provider=provider,
            transaction_type=transaction_type,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return {
            "activities": [activity.to_dict() for activity in activities],
            "pagination": {"limit": limit, "offset": offset, "total": total},
        }

    @app.get("/api/instruments/search")
    async def search_instruments(query: str = "") -> dict[str, object]:
        instruments = await market_data.search_instruments(query)
        return {"instruments": [instrument.to_dict() for instrument in instruments]}

    @app.get("/api/instruments/{instrument_id}/quote")
    async def get_quote(instrument_id: str) -> dict[str, object]:
        quote = await market_data.get_quote(instrument_id)
        return {"quote": quote.to_dict()}

    @app.post("/api/order-drafts")
    async def create_order_draft(request: CreateOrderDraftRequest) -> dict[str, object]:
        draft = await draft_service.create(**request.model_dump())
        return {"draft": draft.to_dict()}

    @app.get("/api/order-drafts/{draft_id}")
    async def get_order_draft(draft_id: str) -> dict[str, object]:
        draft = repository.order_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Order draft not found")
        return {"draft": draft.to_dict()}

    @app.post("/api/order-drafts/{draft_id}/confirm")
    async def confirm_order_draft(
        draft_id: str, request: ConfirmOrderDraftRequest
    ) -> dict[str, object]:
        order = await submission_service.confirm(
            draft_id, request.expected_fingerprint, request.confirmed
        )
        return {"order": order.to_dict()}

    @app.get("/api/orders/{order_id}")
    async def get_order(order_id: str) -> dict[str, object]:
        order = repository.order(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return {"order": order.to_dict()}

    @app.post("/api/refresh")
    async def refresh_portfolio() -> dict[str, dict[str, object]]:
        result = await refresh_service.refresh()
        return {"refresh": result.to_dict()}

    @app.exception_handler(InstrumentNotFoundError)
    async def instrument_not_found(
        _: Request, error: InstrumentNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "instrument_not_found", "message": str(error)}},
        )

    @app.exception_handler(ProviderError)
    async def provider_error(_: Request, error: ProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "provider_error", "message": str(error)}},
        )

    @app.exception_handler(TradingValidationError)
    async def trading_validation_error(
        _: Request, error: TradingValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    return app
