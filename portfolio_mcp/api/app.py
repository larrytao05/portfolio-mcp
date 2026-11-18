import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from portfolio_mcp.account_order_reader import AccountOrderReader
from portfolio_mcp.alpaca_market_data import AlpacaMarketDataProvider
from portfolio_mcp.alpaca_portfolio import AlpacaPortfolioProvider
from portfolio_mcp.alpaca_trading_policy import AlpacaTradingPolicy
from portfolio_mcp.authorization_records import McpAuthorizationError
from portfolio_mcp.bootstrap import (
    create_execution_settings,
    create_schwab_settings,
    resolve_submission_validator,
)
from portfolio_mcp.cancellation_service import CancellationService
from portfolio_mcp.config import ExecutionSettings, SchwabSettings
from portfolio_mcp.connection_store import ConnectionBusyError, ConnectionStore
from portfolio_mcp.database import Database
from portfolio_mcp.direct_schwab_orders import DirectSchwabOrders
from portfolio_mcp.execution import (
    ExecutionProvider,
    FixtureExecutionProvider,
    OrderReadProvider,
    OrderState,
)
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.mcp_authorization import McpAuthorizationService
from portfolio_mcp.order_history import (
    OrderAuditFilters,
    OrderListFilters,
    decode_order_cursor,
    decode_order_event_cursor,
    encode_order_event_cursor,
)
from portfolio_mcp.order_query_store import (
    CorruptedAuditRecordError,
    list_order_events,
    order_draft,
    order_draft_exists,
    order_exists,
    order_for_draft,
)
from portfolio_mcp.order_query_store import list_orders as query_orders
from portfolio_mcp.order_query_store import order as load_order
from portfolio_mcp.order_reconciliation import (
    OrderReconciliationService,
    format_order_page_response,
)
from portfolio_mcp.order_submission_store import recover_stranded_submissions
from portfolio_mcp.overview import OverviewService
from portfolio_mcp.portfolio_store import (
    account_capability,
    account_detail,
    account_exists,
    current_capabilities,
    daily_values,
    list_activities,
    provider_health,
    read_portfolio_view,
)
from portfolio_mcp.portfolio_store import latest_refresh as load_latest_refresh
from portfolio_mcp.portfolio_store import list_positions as load_positions
from portfolio_mcp.portfolio_store import stored_account as load_stored_account
from portfolio_mcp.provider import (
    BrokerOrderProvider,
    InstrumentNotFoundError,
    IntradayMarketDataProvider,
    MarketDataProvider,
    PortfolioProvider,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTLSConfigurationError,
    ProviderTLSVerificationError,
    ProviderUnavailableError,
    SchwabAuthorizationCodeRejectedError,
    SchwabClientAuthenticationError,
    SchwabReauthorizationRequiredError,
)
from portfolio_mcp.provider_registry import ProviderRegistry
from portfolio_mcp.refresh import PortfolioRefreshService
from portfolio_mcp.schwab_mapping_store import (
    AccountNotFoundError,
    SchwabAccountMappingConflictError,
    delete_schwab_account_mapping,
    get_schwab_account_mapping,
    list_schwab_account_mappings,
)
from portfolio_mcp.schwab_readiness import SchwabReadinessService
from portfolio_mcp.schwab_transport import SchwabOAuthTransport
from portfolio_mcp.snaptrade import SnapTradeProvider
from portfolio_mcp.submission_locks import SubmissionLockError
from portfolio_mcp.trading_safety import (
    TradeIntent,
    TradingGuard,
    TradingSettingsError,
    TradingSettingsService,
    settings_dict,
)
from portfolio_mcp.trading_service import (
    OrderCancellationRequestService,
    OrderCancellationService,
    OrderDraftService,
    OrderSubmissionService,
    SubmissionValidator,
    TradingValidationError,
)


class UpdateAccountConnectionRequest(BaseModel):
    version: int = Field(ge=0)
    included: StrictBool | None = None
    trade_enabled: StrictBool | None = None


class SaveSchwabMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=128)
    confirmed: StrictBool


class _SchwabProviderHTTPException(HTTPException):
    def __init__(self, status_code: int, error: ProviderError, message: str) -> None:
        self.provider_code = error.code
        super().__init__(status_code=status_code, detail=message)


def _provider_status_code(error: ProviderError) -> int:
    if isinstance(error, (ProviderAuthenticationError, ProviderAuthorizationError)):
        return 403
    if isinstance(error, ProviderRateLimitError):
        return 429
    if isinstance(error, ProviderUnavailableError):
        return 503
    if isinstance(error, ProviderConfigurationError):
        return 400
    return 502


def _schwab_provider_message(error: ProviderError) -> str:
    if isinstance(
        error,
        (
            ProviderTLSVerificationError,
            ProviderTLSConfigurationError,
            SchwabAuthorizationCodeRejectedError,
            SchwabClientAuthenticationError,
            SchwabReauthorizationRequiredError,
        ),
    ):
        return str(error)
    if isinstance(error, ProviderAuthenticationError):
        return "Unable to authenticate with Schwab"
    if isinstance(error, ProviderAuthorizationError):
        return "Schwab access is not authorized"
    if isinstance(error, ProviderRateLimitError):
        return "Schwab rate limit reached"
    if isinstance(error, ProviderUnavailableError):
        return "Schwab is temporarily unavailable"
    if isinstance(error, ProviderConfigurationError):
        return "Schwab configuration is invalid"
    if isinstance(error, ProviderResponseError):
        return "Schwab returned an invalid response"
    return "Unable to process Schwab mapping"


def _schwab_mapping_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AccountNotFoundError):
        return HTTPException(status_code=404, detail="Portfolio account was not found")
    if isinstance(exc, SchwabAccountMappingConflictError):
        return HTTPException(status_code=409, detail="Schwab account is already mapped")
    if isinstance(exc, ProviderError):
        return _SchwabProviderHTTPException(
            status_code=_provider_status_code(exc),
            error=exc,
            message=_schwab_provider_message(exc),
        )
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail="Invalid Schwab mapping request")
    return HTTPException(status_code=500, detail="Unable to process Schwab mapping")


class CreateCancellationMcpAuthorizationRequest(BaseModel):
    expected_fingerprint: str
    confirmed: StrictBool


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
    configured_providers: dict[str, PortfolioProvider] | None = None,
    direct_schwab_orders: DirectSchwabOrders | None = None,
    execution_provider: ExecutionProvider | None = None,
    order_read_provider: OrderReadProvider | None = None,
    submission_validator: SubmissionValidator | None = None,
    database_url: str = "sqlite:///portfolio.db",
    clock: Callable[[], datetime] | None = None,
    draft_service: OrderDraftService | None = None,
    submission_service: OrderSubmissionService | None = None,
    cancellation_service: OrderCancellationService | None = None,
    cancellation_request_service: OrderCancellationRequestService | None = None,
    mcp_auth_service: McpAuthorizationService | None = None,
    schwab_readiness_service: SchwabReadinessService | None = None,
    execution_settings: ExecutionSettings | None = None,
    schwab_settings: SchwabSettings | None = None,
) -> FastAPI:

    service_clock = clock or (lambda: datetime.now(UTC))
    database = Database(database_url, service_clock)
    repository = ConnectionStore(database)
    sources = dict(configured_providers or {})
    if type(provider) is FixturePortfolioProvider:
        sources.setdefault("fixture", provider)
    registry = ProviderRegistry(repository, sources) if sources else None
    effective_provider = registry or provider
    exec_settings = (
        execution_settings
        if execution_settings is not None
        else create_execution_settings()
    )
    schwab_conf = (
        schwab_settings if schwab_settings is not None else create_schwab_settings()
    )

    schwab_transport = (
        SchwabOAuthTransport(schwab_conf, context="Schwab Trader API")
        if schwab_conf is not None
        else None
    )
    readiness_service = (
        schwab_readiness_service
        if schwab_readiness_service is not None
        else SchwabReadinessService(
            database,
            schwab_transport,
            exec_settings,
            schwab_conf,
        )
    )
    refresh_service = PortfolioRefreshService(
        effective_provider, database, service_clock
    )
    market_data = market_data_provider or FixtureMarketDataProvider()
    execution = (
        execution_provider
        if execution_provider is not None
        else FixtureExecutionProvider(clock=service_clock)
    )
    validator = resolve_submission_validator(
        effective_provider, execution, submission_validator
    )
    settings_service = TradingSettingsService(database, service_clock)
    trading_guard = TradingGuard(database, settings_service)
    active_mcp_auth = (
        mcp_auth_service
        if mcp_auth_service is not None
        else McpAuthorizationService(database, clock=service_clock)
    )
    draft_service = (
        draft_service
        if draft_service is not None
        else OrderDraftService(database, market_data, service_clock, trading_guard)
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
                cancel_dispatch=lambda dispatch: repository.dispatch_cancellation(
                    route, dispatch
                ),
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

    imported_cancellation_service = CancellationService(
        repository, execution_for_account, service_clock
    )
    if direct_schwab_orders is None and schwab_transport is not None:
        direct_schwab_orders = DirectSchwabOrders(database, schwab_transport)

    recover_on_reads = submission_service is None
    submission_service = (
        submission_service
        if submission_service is not None
        else OrderSubmissionService(
            database,
            execution,
            service_clock,
            validate_submission,
            trading_guard,
            mcp_auth_service=active_mcp_auth,
            provider_for_account=execution_for_account,
        )
    )
    active_cancellation_service = (
        cancellation_service
        if cancellation_service is not None
        else OrderCancellationService(
            database,
            execution,
            service_clock,
            provider_for_account=execution_for_account,
        )
    )
    active_cancellation_request_service = (
        cancellation_request_service
        if cancellation_request_service is not None
        else OrderCancellationRequestService(
            database,
            execution,
            service_clock,
            provider_for_account=execution_for_account,
        )
    )
    order_reader = order_read_provider
    if order_reader is None and type(execution) is FixtureExecutionProvider:
        order_reader = execution
    alpaca_source = sources.get("alpaca")
    if order_read_provider is None and isinstance(
        alpaca_source, AlpacaPortfolioProvider
    ):
        order_reader = AccountOrderReader(database, alpaca_source, order_reader)
    reconciliation = (
        OrderReconciliationService(database, order_reader, service_clock)
        if order_reader is not None
        else None
    )
    if recover_on_reads:
        recover_stranded_submissions(database, service_clock())

    def recover_before_order_read() -> None:
        if not recover_on_reads:
            return
        try:
            recover_stranded_submissions(database, service_clock())
        except SubmissionLockError as error:
            raise HTTPException(
                status_code=503, detail="Order recovery is unavailable"
            ) from error

    overview_service = OverviewService(database)
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
                    await asyncio.to_thread(
                        repository.disconnect_connection, route.connection_id
                    )
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
        if not await asyncio.to_thread(repository.disconnect_connection, connection_id):
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
                        if (stored := load_stored_account(database, route.account_id))
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
        changed = await asyncio.to_thread(
            repository.change_account_settings,
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
        accounts = list(read_portfolio_view(database, included_only=True).accounts)

        return {"accounts": [account.to_dict() for account in accounts]}

    @app.get("/api/accounts/{account_id}/positions")
    async def list_positions(account_id: str) -> dict[str, object]:
        positions = load_positions(database, account_id)
        if positions is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"positions": [position.to_dict() for position in positions]}

    @app.get("/api/accounts/{account_id}")
    async def get_account(account_id: str) -> dict[str, object]:
        account = account_detail(database, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"account": account.to_dict()}

    @app.get("/api/accounts/{account_id}/daily-values")
    async def list_daily_values(account_id: str) -> dict[str, object]:
        values = daily_values(database, account_id)
        if values is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"daily_values": [value.to_dict() for value in values]}

    @app.get("/api/trading/status")
    async def trading_status() -> dict[str, object]:
        return {
            "providers": [health.to_dict() for health in provider_health(database)],
            "accounts": [
                capability.to_dict() for capability in current_capabilities(database)
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
        capability = account_capability(database, account_id)
        if capability is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "account_not_found", "message": "Account not found"},
            )
        return {"capability": capability.to_dict()}

    @app.get("/api/schwab/mapping")
    async def list_schwab_mappings() -> dict[str, object]:
        return {
            "mappings": [
                mapping.to_dict() for mapping in list_schwab_account_mappings(database)
            ]
        }

    @app.get("/api/schwab/mapping/candidates")
    async def list_schwab_candidates(
        account_id: Annotated[str, Query(min_length=1)],
    ) -> dict[str, object]:
        try:
            candidates = await readiness_service.list_candidates_for_account(account_id)
            return {
                "account_id": account_id,
                "candidates": [candidate.to_dict() for candidate in candidates],
            }
        except Exception as exc:
            raise _schwab_mapping_error(exc) from exc

    @app.get("/api/schwab/mapping/{account_id}")
    async def get_schwab_mapping(account_id: str) -> dict[str, object]:
        mapping = get_schwab_account_mapping(database, account_id)
        if mapping is None:
            raise HTTPException(
                status_code=404,
                detail="No Schwab mapping exists for this portfolio account",
            )
        return {"mapping": mapping.to_dict()}

    @app.post("/api/schwab/mapping/{account_id}")
    async def save_schwab_mapping(
        account_id: str, request: SaveSchwabMappingRequest
    ) -> dict[str, object]:
        try:
            mapping = await readiness_service.save_verified_mapping(
                account_id=account_id,
                candidate_id=request.candidate_id,
                confirmed=request.confirmed,
            )
            return {"mapping": mapping.to_dict()}
        except Exception as exc:
            raise _schwab_mapping_error(exc) from exc

    @app.delete("/api/schwab/mapping/{account_id}")
    async def delete_schwab_mapping(account_id: str) -> dict[str, object]:
        deleted = delete_schwab_account_mapping(database, account_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="No Schwab mapping exists for this portfolio account",
            )
        return {"deleted": True, "account_id": account_id}

    @app.get("/api/schwab/readiness")
    async def all_schwab_readiness() -> dict[str, object]:
        readiness_list = await readiness_service.check_all_readiness()
        return {"readiness": [r.to_dict() for r in readiness_list]}

    @app.get("/api/schwab/readiness/{account_id}")
    async def account_schwab_readiness(account_id: str) -> dict[str, object]:
        readiness = await readiness_service.check_account_readiness(account_id)
        return {"readiness": readiness.to_dict()}

    @app.get("/api/refreshes/latest")
    async def latest_refresh() -> dict[str, object]:
        result = load_latest_refresh(database)
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
        activities, total = list_activities(
            database,
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
            stored = load_stored_account(database, account_id)
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
                order.to_dict()
                for order in query_orders(
                    database,
                    account_id=account_id,
                    states=(
                        OrderState.UNKNOWN,
                        OrderState.SUBMITTING,
                        OrderState.CANCEL_PENDING,
                    ),
                ).items
            ]
        }

    @app.get("/api/accounts/{account_id}/cancellations")
    async def unresolved_cancellations(account_id: str) -> dict[str, object]:
        route = repository.account_route(account_id)
        if route is None or not route.active or not route.included:
            raise HTTPException(404, "Account is unavailable")
        return {"cancellations": repository.unresolved_cancellations(account_id)}

    @app.get("/api/accounts/{account_id}/cancellation-attempts")
    async def cancellation_attempts(account_id: str) -> dict[str, object]:
        route = repository.account_route(account_id)
        if route is None or not route.active or not route.included:
            raise HTTPException(404, "Account is unavailable")
        return {
            "attempts": repository.cancellation_attempts(account_id),
            "events": repository.cancellation_events(account_id),
        }

    @app.post("/api/accounts/{account_id}/broker-orders/{broker_order_id}/cancel")
    async def cancel_broker_order(
        account_id: str, broker_order_id: str, request: ConfirmCancellationRequest
    ) -> dict[str, str]:
        return {
            "state": await imported_cancellation_service.cancel(
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
            "state": await imported_cancellation_service.reconcile(
                account_id, broker_order_id
            )
        }

    @app.get("/api/order-drafts/{draft_id}")
    async def get_order_draft(draft_id: str) -> dict[str, object]:
        draft = order_draft(database, draft_id)
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
        draft = order_draft(database, draft_id)
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
        existing = order_for_draft(database, draft_id)
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

    @app.get("/api/cancellation-requests/{request_id}")
    async def get_cancellation_request(request_id: str) -> dict[str, object]:
        req = active_cancellation_request_service.get_request(request_id)
        if req is None:
            raise HTTPException(
                status_code=404, detail="Cancellation request not found"
            )
        return {"cancellation_request": req.to_dict()}

    @app.post("/api/cancellation-requests/{request_id}/mcp-authorization")
    async def create_cancellation_mcp_authorization(
        request_id: str, request: CreateCancellationMcpAuthorizationRequest
    ) -> dict[str, object]:
        if not request.confirmed:
            raise HTTPException(
                status_code=422,
                detail="Explicit confirmation is required",
            )
        try:
            stored_req, created = active_mcp_auth.authorize_cancellation_request(
                request_id=request_id,
                expected_fingerprint=request.expected_fingerprint,
                now=service_clock(),
            )
            return {
                "authorization_id": created.id,
                "code": created.plaintext_code,
                "expires_at": created.expires_at.isoformat(),
                "cancellation_request": stored_req.to_dict(),
            }
        except McpAuthorizationError as error:
            if error.code == "cancellation_request_not_found":
                raise HTTPException(
                    status_code=404, detail="Cancellation request not found"
                )
            raise HTTPException(status_code=409, detail=error.message)

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
        recover_before_order_read()
        if start_date is not None and end_date is not None and start_date > end_date:
            raise HTTPException(
                status_code=422, detail="start_date must be on or before end_date"
            )
        if account_id is not None and not account_exists(database, account_id):
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
        page = query_orders(
            database,
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
        if order_id is not None and not order_exists(database, order_id):
            raise HTTPException(status_code=404, detail="Order not found")
        if draft_id is not None and not order_draft_exists(database, draft_id):
            raise HTTPException(status_code=404, detail="Order draft not found")
        if account_id is not None and not account_exists(database, account_id):
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
            page = list_order_events(
                database,
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
        recover_before_order_read()
        if reconciliation is None:
            if load_order(database, order_id) is None:
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
        order = await active_cancellation_service.cancel(
            order_id=order_id,
            expected_version=request.expected_version,
            expected_state=request.expected_state,
            confirmed=request.confirmed,
        )
        return {"order": order.to_dict()}

    @app.get("/api/orders/{order_id}")
    async def get_order(order_id: str) -> dict[str, object]:
        recover_before_order_read()
        order = load_order(database, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return {"order": order.to_dict()}

    @app.post("/api/orders/{order_id}/reconcile")
    async def reconcile_order(order_id: str) -> dict[str, object]:
        if reconciliation is None:
            raise HTTPException(
                status_code=503, detail="Order reconciliation is unavailable"
            )
        try:
            return {
                "order": (await reconciliation.reconcile_unknown(order_id)).to_dict()
            }
        except ValueError as error:
            if str(error) == "Order not found":
                raise HTTPException(
                    status_code=404, detail="Order not found"
                ) from error
            raise

    @app.post("/api/refresh")
    async def refresh_portfolio() -> dict[str, dict[str, object]]:
        if (
            type(provider) is FixturePortfolioProvider
            and not repository.account_routes()
        ):
            await connect_provider("fixture")
        result = await refresh_service.refresh()
        return {"refresh": result.to_dict()}

    @app.exception_handler(ConnectionBusyError)
    async def connection_busy(_: Request, error: ConnectionBusyError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

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
            status_code=_provider_status_code(error),
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.exception_handler(_SchwabProviderHTTPException)
    async def schwab_provider_http_error(
        _: Request, error: _SchwabProviderHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "detail": error.detail,
                "error": {"code": error.provider_code, "message": error.detail},
            },
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
