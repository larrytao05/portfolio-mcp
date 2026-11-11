from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool

from portfolio_mcp.alpaca_market_data import AlpacaMarketDataProvider
from portfolio_mcp.alpaca_portfolio import AlpacaPortfolioProvider
from portfolio_mcp.alpaca_trading_policy import AlpacaTradingPolicy
from portfolio_mcp.cancellation_service import CancellationService
from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.direct_schwab_orders import DirectSchwabOrders
from portfolio_mcp.execution import ExecutionProvider, FixtureExecutionProvider
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.overview import OverviewService
from portfolio_mcp.provider import (
    BrokerOrderProvider,
    InstrumentNotFoundError,
    IntradayMarketDataProvider,
    MarketDataProvider,
    PortfolioProvider,
    ProviderError,
)
from portfolio_mcp.provider_registry import ProviderRegistry
from portfolio_mcp.refresh import PortfolioRefreshService
from portfolio_mcp.snaptrade import SnapTradeProvider
from portfolio_mcp.trading_safety import (
    TradeIntent,
    TradingGuard,
    TradingSettingsError,
    TradingSettingsService,
    settings_dict,
)
from portfolio_mcp.trading_service import (
    OrderDraftService,
    OrderSubmissionService,
    SubmissionValidator,
    TradingValidationError,
    fixture_submission_validator,
)


class UpdateAccountConnectionRequest(BaseModel):
    version: int = Field(ge=0)
    included: StrictBool | None = None
    trade_enabled: StrictBool | None = None


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


class ConfirmCancellationRequest(BaseModel):
    confirmed: StrictBool


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
    configured_providers: dict[str, PortfolioProvider] | None = None,
    direct_schwab_orders: DirectSchwabOrders | None = None,
    execution_provider: ExecutionProvider | None = None,
    submission_validator: SubmissionValidator | None = None,
    database_url: str = "sqlite:///portfolio.db",
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    repository = PortfolioRepository(database_url, clock)
    sources = dict(configured_providers or {})
    if type(provider) is FixturePortfolioProvider:
        sources.setdefault("fixture", provider)
    registry = ProviderRegistry(repository, sources) if sources else None
    effective_provider = registry or provider
    refresh_service = PortfolioRefreshService(effective_provider, repository, clock)
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
        validator = fixture_submission_validator(effective_provider)
    settings_service = TradingSettingsService(repository, service_clock)
    trading_guard = TradingGuard(repository, settings_service)
    draft_service = OrderDraftService(
        repository, market_data, service_clock, trading_guard
    )

    def execution_for_account(account_id: str) -> ExecutionProvider:
        route = repository.account_route(account_id)
        if (
            route is None
            or not route.active
            or not route.included
            or not route.trade_enabled
        ):
            raise TradingValidationError(
                "account_route_unavailable", "The account is not enabled for trading"
            )
        if route.provider_route == "fixture":
            return execution
        alpaca = sources.get("alpaca")
        if (
            route.provider_route == "alpaca"
            and route.credential_reference == "server_env:alpaca"
            and isinstance(alpaca, AlpacaPortfolioProvider)
        ):

            def dispatch_guard(command):
                decision = trading_guard.evaluate(
                    TradeIntent(
                        command.account_id,
                        command.symbol,
                        command.asset_class,
                        command.side,
                        command.order_type,
                        command.quantity,
                        command.limit_price,
                        command.guard_bid_price,
                        command.guard_ask_price,
                        command.guard_last_price,
                    )
                )
                if not decision.allowed:
                    raise TradingValidationError(
                        decision.violations[0].code, decision.violations[0].message
                    )

            return alpaca.execution_provider(
                account_id,
                route.upstream_account_id,
                dispatch_guard=dispatch_guard,
                clock=service_clock,
            )
        raise TradingValidationError(
            "execution_unsupported", "This account cannot submit orders"
        )

    fallback_validator = validator

    async def validate_submission(draft, now):
        route = repository.account_route(draft.account_id)
        if route is not None and route.provider_route == "alpaca":
            alpaca = sources.get("alpaca")
            if not isinstance(alpaca, AlpacaPortfolioProvider) or not isinstance(
                market_data, AlpacaMarketDataProvider
            ):
                raise TradingValidationError(
                    "trading_state_unavailable", "Current trading state is unavailable"
                )
            return await AlpacaTradingPolicy(
                repository, alpaca, market_data, trading_guard, service_clock
            )(draft, now)
        return await fallback_validator(draft, now)

    submission_service = OrderSubmissionService(
        repository,
        execution,
        service_clock,
        validate_submission,
        trading_guard,
        provider_for_account=execution_for_account,
    )
    cancellation_service = CancellationService(
        repository, execution_for_account, service_clock
    )
    overview_service = OverviewService(repository)
    app = FastAPI(title="Portfolio Dashboard API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=[],
    )

    @app.post("/api/connections/snaptrade/portal")
    async def snaptrade_portal(
        broker: str | None = None, reconnect: str | None = None
    ) -> dict[str, str]:
        source = sources.get("snaptrade")
        if not isinstance(source, SnapTradeProvider):
            raise HTTPException(status_code=503, detail="SnapTrade is not configured")
        if broker not in (None, "SCHWAB", "FIDELITY"):
            raise HTTPException(status_code=422, detail="Unsupported brokerage")
        try:
            return {
                "url": await source.connection_portal_url(
                    broker=broker, reconnect=reconnect
                )
            }
        except ProviderError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None

    @app.post("/api/connections/{provider_route}")
    async def connect_provider(provider_route: str) -> dict[str, object]:
        source = sources.get(provider_route)
        if source is None:
            raise HTTPException(status_code=503, detail="Provider is not configured")
        try:
            accounts = await source.list_accounts()
        except ProviderError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        if isinstance(source, SnapTradeProvider):
            try:
                discovered = await source.connection_accounts()
            except ProviderError as error:
                raise HTTPException(status_code=503, detail=str(error)) from None
            known_status = {
                f"snaptrade:{external_id}": active
                for external_id, active, _ in discovered
            }
            for route in repository.account_routes():
                if (
                    route.provider_route == "snaptrade"
                    and route.active
                    and not known_status.get(route.connection_id, False)
                ):
                    repository.disconnect_connection(route.connection_id)
            account_ids = {account.id for account in accounts}
            seen: set[str] = set()
            existing = {route.connection_id for route in repository.account_routes()}
            for external_id, active, ids in discovered:
                connection_id = f"snaptrade:{external_id}"
                selected = account_ids.intersection(ids)
                if seen.intersection(selected):
                    raise HTTPException(
                        status_code=503, detail="Ambiguous account connection"
                    )
                seen.update(selected)
                if connection_id not in existing:
                    repository.save_connection(
                        connection_id,
                        "snaptrade",
                        "live",
                        "server_env:snaptrade",
                        active,
                    )
                for account_id in selected:
                    if repository.account_route(account_id) is None:
                        repository.save_account_route(
                            account_id, connection_id, account_id
                        )
        else:
            environment = "fixture" if provider_route == "fixture" else "live"
            reference = (
                "fixture"
                if provider_route == "fixture"
                else f"server_env:{provider_route}"
            )
            repository.save_connection(
                provider_route, provider_route, environment, reference, True
            )
            for account in accounts:
                account_id = (
                    f"alpaca:{account.id}" if provider_route == "alpaca" else account.id
                )
                if repository.account_route(account_id) is None:
                    repository.save_account_route(
                        account_id, provider_route, account.id
                    )
        return {"accounts": [account.to_dict() for account in accounts]}

    @app.post("/api/connections/{connection_id}/reactivate")
    async def reactivate_connection(connection_id: str) -> dict[str, object]:
        routes = [
            route
            for route in repository.account_routes()
            if route.connection_id == connection_id
        ]
        if not routes or not connection_id.startswith("snaptrade:"):
            raise HTTPException(status_code=404, detail="Connection not found")
        source = sources.get("snaptrade")
        if not isinstance(source, SnapTradeProvider):
            raise HTTPException(status_code=503, detail="SnapTrade is not configured")
        try:
            discovered = await source.connection_accounts()
        except ProviderError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        matches = [
            (active, ids)
            for external_id, active, ids in discovered
            if f"snaptrade:{external_id}" == connection_id
        ]
        if (
            len(matches) != 1
            or not matches[0][0]
            or any(route.upstream_account_id not in matches[0][1] for route in routes)
        ):
            raise HTTPException(
                status_code=409,
                detail="Connection is not authorized or its accounts have changed",
            )
        repository.save_connection(
            connection_id, "snaptrade", "live", "server_env:snaptrade", True
        )
        return {"status": "connected", "trade_permission": "disabled"}

    @app.post("/api/connections/{connection_id}/disconnect")
    async def disconnect_connection(connection_id: str) -> dict[str, object]:
        if not repository.disconnect_connection(connection_id):
            raise HTTPException(status_code=404, detail="Connection not found")
        return {"status": "disconnected", "remote_revoked": False}

    @app.get("/api/connections")
    async def list_connections() -> dict[str, object]:
        routes = repository.account_routes()
        return {
            "configured": {
                key: key in sources for key in ("alpaca", "snaptrade", "fixture")
            },
            "accounts": [
                {
                    "account_id": route.account_id,
                    "label": (
                        stored.account.label
                        if (stored := repository.stored_account(route.account_id))
                        else "Account awaiting refresh"
                    ),
                    "connection_id": route.connection_id,
                    "provider_route": route.provider_route,
                    "active": route.active,
                    "included": route.included,
                    "trade_enabled": route.trade_enabled,
                    "version": route.version,
                }
                for route in routes
            ],
        }

    @app.put("/api/accounts/{account_id}/connection-settings")
    async def update_account_connection(
        account_id: str, request: UpdateAccountConnectionRequest
    ) -> dict[str, object]:
        if (request.included is None) == (request.trade_enabled is None):
            raise HTTPException(status_code=422, detail="Change exactly one setting")
        changed = repository.change_account_settings(
            account_id,
            request.version,
            included=request.included,
            trade_enabled=request.trade_enabled,
        )
        if not changed:
            raise HTTPException(
                status_code=409, detail="Connection changed; refresh and try again"
            )
        return {"account_id": account_id}

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
        routes = {route.account_id: route for route in repository.account_routes()}
        accounts = [
            account
            for account in accounts
            if (route := routes.get(account.account.id)) is not None
            and route.active
            and route.included
        ]
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

    @app.get("/api/instruments/{instrument_id}/intraday")
    async def get_intraday(
        instrument_id: str, extended_hours: bool = False
    ) -> dict[str, object]:
        if not isinstance(market_data, IntradayMarketDataProvider):
            raise HTTPException(501, "Intraday data is not supported by this provider")
        return {
            "history": (
                await market_data.get_intraday(instrument_id, extended_hours)
            ).to_dict()
        }

    @app.get("/api/accounts/{account_id}/broker-orders")
    async def get_broker_orders(
        account_id: str,
        symbol: str = Query(min_length=1, max_length=32),
        status: Literal["open", "closed", "all"] = "all",
    ) -> dict[str, object]:
        if direct_schwab_orders and direct_schwab_orders.has_account(account_id):
            route = await registry._verified_route(account_id) if registry else None
            stored = repository.stored_account(account_id)
            if (
                route is None
                or route.provider_route != "snaptrade"
                or not route.active
                or not route.included
                or stored is None
            ):
                raise HTTPException(404, "Account is unavailable")
            return {
                "history": (
                    await direct_schwab_orders.get_orders(
                        stored.account, symbol, status
                    )
                ).to_dict()
            }
        if not isinstance(effective_provider, BrokerOrderProvider):
            raise HTTPException(
                501, "Broker order history is not supported by this provider"
            )
        return {
            "history": (
                await effective_provider.get_broker_orders(account_id, symbol, status)
            ).to_dict()
        }

    @app.get("/api/accounts/{account_id}/unresolved-orders")
    async def unresolved_orders(account_id: str) -> dict[str, object]:
        route = repository.account_route(account_id)
        if route is None or not route.active or not route.included:
            raise HTTPException(404, "Account is unavailable")
        return {
            "orders": [
                order.to_dict() for order in repository.unresolved_orders(account_id)
            ]
        }

    @app.get("/api/accounts/{account_id}/cancellations")
    async def unresolved_cancellations(account_id: str) -> dict[str, object]:
        route = repository.account_route(account_id)
        if route is None or not route.active or not route.included:
            raise HTTPException(404, "Account is unavailable")
        return {"cancellations": repository.unresolved_cancellations(account_id)}

    @app.post("/api/accounts/{account_id}/broker-orders/{broker_order_id}/cancel")
    async def cancel_broker_order(
        account_id: str, broker_order_id: str, request: ConfirmCancellationRequest
    ) -> dict[str, str]:
        return {
            "state": await cancellation_service.cancel(
                account_id, broker_order_id, request.confirmed
            )
        }

    @app.post(
        "/api/accounts/{account_id}/broker-orders/{broker_order_id}/reconcile-cancellation"
    )
    async def reconcile_cancellation(
        account_id: str, broker_order_id: str
    ) -> dict[str, str]:
        return {
            "state": await cancellation_service.reconcile(account_id, broker_order_id)
        }

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

    @app.post("/api/orders/{order_id}/reconcile")
    async def reconcile_order(order_id: str) -> dict[str, object]:
        return {"order": (await submission_service.reconcile(order_id)).to_dict()}

    @app.post("/api/refresh")
    async def refresh_portfolio() -> dict[str, dict[str, object]]:
        if (
            type(provider) is FixturePortfolioProvider
            and not repository.account_routes()
        ):
            await connect_provider("fixture")
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
