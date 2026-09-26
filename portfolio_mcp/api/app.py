from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from portfolio_mcp.database import CorruptedAuditRecordError, PortfolioRepository
from portfolio_mcp.execution import (
    ExecutionProvider,
    FixtureExecutionProvider,
    OrderReadProvider,
    OrderState,
)
from portfolio_mcp.fixtures import FixtureMarketDataProvider
from portfolio_mcp.order_history import (
    OrderAuditFilters,
    OrderListFilters,
    decode_order_cursor,
    decode_order_event_cursor,
    encode_order_event_cursor,
)
from portfolio_mcp.order_reconciliation import (
    OrderReconciliationService,
    format_order_page_response,
)
from portfolio_mcp.overview import OverviewService
from portfolio_mcp.provider import (
    InstrumentNotFoundError,
    MarketDataProvider,
    PortfolioProvider,
    ProviderError,
)
from portfolio_mcp.refresh import PortfolioRefreshService
from portfolio_mcp.trading_safety import (
    TradingGuard,
    TradingSettingsError,
    TradingSettingsService,
    settings_dict,
)
from portfolio_mcp.trading_service import (
    McpAuthorizationError,
    McpAuthorizationService,
    OrderCancellationService,
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


class CreateMcpAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_fingerprint: str = Field(min_length=1, max_length=128)
    confirmed: StrictBool


class ConfirmOrderCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    expected_state: OrderState
    confirmed: bool


class RefreshOrderRequest(BaseModel):
    mode: Literal["manual", "scheduled"] = "manual"


class UpdateTradingSettingsRequest(BaseModel):
    live_trading_enabled: StrictBool
    kill_switch_active: StrictBool
    max_order_shares: str | None
    max_order_notional_usd: str | None
    version: int = Field(ge=0)


def create_app(
    provider: PortfolioProvider,
    *,
    market_data_provider: MarketDataProvider | None = None,
    execution_provider: ExecutionProvider | None = None,
    order_read_provider: OrderReadProvider | None = None,
    submission_validator: SubmissionValidator | None = None,
    database_url: str = "sqlite:///portfolio.db",
    clock: Callable[[], datetime] | None = None,
    draft_service: OrderDraftService | None = None,
    submission_service: OrderSubmissionService | None = None,
    mcp_auth_service: McpAuthorizationService | None = None,
) -> FastAPI:
    service_clock = clock or (lambda: datetime.now(UTC))
    repository = PortfolioRepository(database_url, service_clock)
    refresh_service = PortfolioRefreshService(provider, repository, service_clock)
    market_data = market_data_provider or FixtureMarketDataProvider()
    execution = (
        execution_provider
        if execution_provider is not None
        else FixtureExecutionProvider(clock=service_clock)
    )
    validator = submission_validator
    if validator is None:
        if type(execution) is not FixtureExecutionProvider:
            raise ValueError("A final trading policy validator is required")
        validator = fixture_submission_validator(provider)
    settings_service = TradingSettingsService(repository, service_clock)
    trading_guard = TradingGuard(repository, settings_service)
    active_mcp_auth = (
        mcp_auth_service
        if mcp_auth_service is not None
        else McpAuthorizationService(repository, clock=service_clock)
    )
    draft_service = (
        draft_service
        if draft_service is not None
        else OrderDraftService(repository, market_data, service_clock, trading_guard)
    )
    submission_service = (
        submission_service
        if submission_service is not None
        else OrderSubmissionService(
            repository,
            execution,
            service_clock,
            validator,
            trading_guard,
            mcp_auth_service=active_mcp_auth,
        )
    )
    cancellation_service = OrderCancellationService(
        repository, execution, service_clock
    )
    order_reader = order_read_provider
    if order_reader is None and type(execution) is FixtureExecutionProvider:
        order_reader = execution
    reconciliation = (
        OrderReconciliationService(repository, order_reader, service_clock)
        if order_reader is not None
        else None
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

    @app.get("/api/trading/settings")
    async def trading_settings() -> dict[str, object]:
        return {"settings": settings_dict(settings_service.get())}

    @app.put("/api/trading/settings")
    async def update_trading_settings(
        request: UpdateTradingSettingsRequest,
    ) -> dict[str, object]:
        settings = settings_service.replace(**request.model_dump())
        return {"settings": settings_dict(settings)}

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

    @app.post("/api/order-drafts/{draft_id}/mcp-authorization")
    async def create_mcp_authorization(
        draft_id: str, request: CreateMcpAuthorizationRequest
    ) -> dict[str, object]:
        if not request.confirmed:
            raise HTTPException(
                status_code=422,
                detail="Explicit confirmation is required",
            )
        draft = repository.order_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Order draft not found")
        if draft.fingerprint != request.expected_fingerprint:
            raise HTTPException(
                status_code=409,
                detail="The reviewed draft no longer matches",
            )
        now = service_clock()
        if now > draft.expires_at:
            raise HTTPException(status_code=409, detail="Order draft has expired")
        existing = repository.order_for_draft(draft_id)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail="An order has already been created for this draft",
            )
        try:
            created = active_mcp_auth.create_authorization(
                action="submit", target_draft_id=draft_id
            )
        except McpAuthorizationError as error:
            raise HTTPException(status_code=409, detail=str(error))
        return {
            "authorization_id": created.id,
            "code": created.plaintext_code,
            "expires_at": created.expires_at.isoformat(),
            "draft": {
                "id": draft.id,
                "symbol": draft.symbol,
                "side": draft.side,
                "quantity": str(draft.quantity),
                "order_type": draft.order_type,
                "limit_price": (
                    str(draft.limit_price) if draft.limit_price is not None else None
                ),
                "fingerprint": draft.fingerprint,
            },
        }

    @app.get("/api/orders")
    async def list_orders(
        account_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        provider: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
        symbol: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
        state: Annotated[list[OrderState], Query()] = [],
        start_date: date | None = None,
        end_date: date | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> dict[str, object]:
        if start_date is not None and end_date is not None and start_date > end_date:
            raise HTTPException(
                status_code=422, detail="start_date must be on or before end_date"
            )
        if account_id is not None and not repository.account_exists(account_id):
            raise HTTPException(status_code=404, detail="Account not found")
        normalized_provider = provider.strip() if provider is not None else None
        normalized_symbol = symbol.strip().upper() if symbol is not None else None
        filters = OrderListFilters(
            account_id=account_id,
            provider=normalized_provider,
            symbol=normalized_symbol,
            states=tuple(sorted({value.value for value in state})),
            start_date=start_date,
            end_date=end_date,
        )
        try:
            after = decode_order_cursor(cursor, filters, limit)
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="Invalid order cursor"
            ) from error
        page = repository.list_orders(
            limit=limit,
            after=after,
            account_id=account_id,
            states=tuple(OrderState(value) for value in filters.states),
            provider=normalized_provider,
            symbol=normalized_symbol,
            start_date=start_date,
            end_date=end_date,
        )
        return format_order_page_response(page, reconciliation, service_clock())

    @app.get("/api/order-audit")
    async def list_order_audit(
        order_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        draft_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        account_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        provider: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
        symbol: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
        state: Annotated[list[OrderState], Query()] = [],
        start_date: date | None = None,
        end_date: date | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> dict[str, object]:
        if order_id is not None and draft_id is not None:
            raise HTTPException(status_code=422, detail="Specify order_id or draft_id")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise HTTPException(
                status_code=422, detail="start_date must be on or before end_date"
            )
        if order_id is not None and not repository.order_exists(order_id):
            raise HTTPException(status_code=404, detail="Order not found")
        if draft_id is not None and not repository.order_draft_exists(draft_id):
            raise HTTPException(status_code=404, detail="Order draft not found")
        if account_id is not None and not repository.account_exists(account_id):
            raise HTTPException(status_code=404, detail="Account not found")
        normalized_provider = provider.strip() if provider is not None else None
        normalized_symbol = symbol.strip().upper() if symbol is not None else None
        filters = OrderAuditFilters(
            order_id=order_id,
            draft_id=draft_id,
            account_id=account_id,
            provider=normalized_provider,
            symbol=normalized_symbol,
            states=tuple(sorted({value.value for value in state})),
            start_date=start_date,
            end_date=end_date,
        )
        try:
            after = decode_order_event_cursor(cursor, filters, limit)
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="Invalid order-audit cursor"
            ) from error
        try:
            page = repository.list_order_events(
                limit=limit,
                after=after,
                order_id=order_id,
                draft_id=draft_id,
                account_id=account_id,
                provider=normalized_provider,
                symbol=normalized_symbol,
                states=filters.states,
                start_date=start_date,
                end_date=end_date,
            )
        except CorruptedAuditRecordError as error:
            raise HTTPException(
                status_code=500, detail="Order audit details are unavailable"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "events": [event.to_dict() for event in page.items],
            "next_cursor": encode_order_event_cursor(page.next_cursor),
        }

    @app.post("/api/orders/{order_id}/refresh")
    async def refresh_order(
        order_id: str,
        request: RefreshOrderRequest = Body(default=RefreshOrderRequest()),
    ) -> dict[str, object]:
        if reconciliation is None:
            if repository.order(order_id) is None:
                raise HTTPException(status_code=404, detail="Order not found")
            raise HTTPException(
                status_code=503, detail="Order reconciliation is unavailable"
            )
        try:
            result = await reconciliation.refresh(order_id, mode=request.mode)
        except ValueError as error:
            if str(error) == "Order not found":
                raise HTTPException(
                    status_code=404, detail="Order not found"
                ) from error
            raise
        return {
            "order": result.order.to_dict(),
            "refresh": {
                "status": result.status,
                "provider_read_started": result.provider_read_started,
                "next_refresh_at": (
                    result.next_refresh_at.isoformat()
                    if result.next_refresh_at is not None
                    else None
                ),
                "target_order_id": result.target_order_id,
                "server_time": result.server_time.isoformat(),
            },
        }

    @app.post("/api/orders/{order_id}/cancel/confirm")
    async def confirm_order_cancellation(
        order_id: str, request: ConfirmOrderCancellationRequest
    ) -> dict[str, object]:
        order = await cancellation_service.cancel(
            order_id=order_id,
            expected_version=request.expected_version,
            expected_state=request.expected_state,
            confirmed=request.confirmed,
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
        status_code = (
            404
            if error.code == "order_not_found"
            else 409
            if error.code in {"order_conflict", "stale_version"}
            else 422
        )
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.exception_handler(TradingSettingsError)
    async def trading_settings_error(
        _: Request, error: TradingSettingsError
    ) -> JSONResponse:
        status_code = 409 if error.code == "settings_conflict" else 422
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.exception_handler(RequestValidationError)
    async def trading_settings_request_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        if request.url.path != "/api/trading/settings":
            return await request_validation_exception_handler(request, error)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid trading settings request",
                }
            },
        )

    return app
