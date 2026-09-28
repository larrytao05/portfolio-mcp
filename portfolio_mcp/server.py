from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.execution import (
    FixtureExecutionProvider,
    OrderReadProvider,
    OrderState,
)
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.order_history import (
    OrderListFilters,
    decode_order_cursor,
)
from portfolio_mcp.order_reconciliation import (
    OrderReconciliationService,
    format_order_page_response,
)
from portfolio_mcp.provider import (
    AccountNotFoundError,
    InstrumentNotFoundError,
    MarketDataProvider,
    PortfolioProvider,
)
from portfolio_mcp.trading_safety import (
    TradingGuard,
    TradingSettingsService,
)
from portfolio_mcp.trading_service import (
    OrderDraftService,
    TradingValidationError,
)


def create_server(
    provider: PortfolioProvider,
    *,
    market_data_provider: MarketDataProvider | None = None,
    order_read_provider: OrderReadProvider | None = None,
    database_url: str = "sqlite:///portfolio.db",
    clock: Callable[[], datetime] | None = None,
    repository: PortfolioRepository | None = None,
    draft_service: OrderDraftService | None = None,
    reconciliation_service: OrderReconciliationService | None = None,
) -> MCPServer:
    mcp = MCPServer("portfolio-mcp")

    service_clock = clock or (lambda: datetime.now(UTC))
    repo = repository or PortfolioRepository(database_url, service_clock)
    market_data = market_data_provider or FixtureMarketDataProvider()

    active_draft_service = draft_service
    if active_draft_service is None:
        settings_service = TradingSettingsService(repo, service_clock)
        trading_guard = TradingGuard(repo, settings_service)
        active_draft_service = OrderDraftService(
            repo, market_data, service_clock, trading_guard
        )

    order_reader = (
        order_read_provider
        if order_read_provider is not None
        else FixtureExecutionProvider(clock=service_clock)
    )

    reconciliation = reconciliation_service
    if reconciliation is None and order_reader is not None:
        reconciliation = OrderReconciliationService(repo, order_reader, service_clock)

    @mcp.tool()
    async def list_accounts() -> dict[str, list[dict[str, str]]]:
        """List connected taxable brokerage and Roth IRA accounts. Read-only."""
        accounts = await provider.list_accounts()
        return {"accounts": [account.to_dict() for account in accounts]}

    @mcp.tool()
    async def get_holdings(account_id: str) -> dict[str, object]:
        """Get holdings and reported valuations for a portfolio account. Read-only."""
        try:
            snapshot = await provider.get_holdings(account_id)
            return snapshot.to_dict()
        except AccountNotFoundError as error:
            raise ToolError(f"account_not_found: {error}") from error

    @mcp.tool()
    async def get_transactions(
        account_id: str, start_date: str, end_date: str
    ) -> dict[str, object]:
        """Get account transactions in an inclusive ISO-8601 date range. Read-only."""
        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")
        if start > end:
            raise ToolError(
                "invalid_date_range: start_date must be on or before end_date"
            )
        try:
            history = await provider.get_transactions(account_id, start, end)
            return history.to_dict()
        except AccountNotFoundError as error:
            raise ToolError(f"account_not_found: {error}") from error

    @mcp.tool()
    async def get_trading_status() -> dict[str, object]:
        """Inspect current execution status and capabilities across providers.

        Read-only. Does not place orders, enable trading, or expose owner
        limits, settings, or authorization codes.
        """
        return {
            "providers": [health.to_dict() for health in repo.provider_health()],
            "accounts": [
                capability.to_dict() for capability in repo.current_capabilities()
            ],
        }

    @mcp.tool()
    async def search_instruments(query: str = "") -> dict[str, object]:
        """Search tradable market instruments by symbol or name. Read-only."""
        instruments = await market_data.search_instruments(query)
        return {"instruments": [instrument.to_dict() for instrument in instruments]}

    @mcp.tool()
    async def create_order_draft(
        account_id: str,
        instrument_id: str,
        side: str,
        order_type: str,
        quantity: str,
        limit_price: str | None = None,
    ) -> dict[str, object]:
        """Create an order draft validated against market data and trading guard rules.

        Drafts are not orders and do not execute or place trades.
        Submission requires independent dashboard review and confirmation with
        a one-time authorization code.
        """
        try:
            draft = await active_draft_service.create(
                account_id=account_id,
                instrument_id=instrument_id,
                side=side,
                order_type=order_type,
                quantity=quantity,
                limit_price=limit_price,
            )
            return {"draft": draft.to_dict()}
        except TradingValidationError as error:
            raise ToolError(f"{error.code}: {error}") from error
        except InstrumentNotFoundError as error:
            raise ToolError(f"instrument_not_found: {error}") from error

    @mcp.tool()
    async def get_order_draft(draft_id: str) -> dict[str, object]:
        """Inspect an existing order draft by ID.

        Read-only. Drafts are not orders and do not execute or place trades
        until independently reviewed and confirmed on the dashboard.
        """
        draft = repo.order_draft(draft_id)
        if draft is None:
            raise ToolError(f"draft_not_found: Order draft '{draft_id}' not found")
        return {"draft": draft.to_dict()}

    @mcp.tool()
    async def list_orders(
        account_id: str | None = None,
        provider: str | None = None,
        symbol: str | None = None,
        state: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """List orders matching optional filters with forward-only pagination.

        Read-only. Does not place or modify orders. Does not expose raw provider
        responses or broker account hashes.
        """
        start = _parse_date(start_date, "start_date") if start_date else None
        end = _parse_date(end_date, "end_date") if end_date else None
        if start is not None and end is not None and start > end:
            raise ToolError(
                "invalid_date_range: start_date must be on or before end_date"
            )
        if account_id is not None and not repo.account_exists(account_id):
            raise ToolError(f"account_not_found: Account '{account_id}' not found")
        if limit < 1 or limit > 100:
            raise ToolError("invalid_limit: limit must be between 1 and 100")

        normalized_provider = provider.strip() if provider is not None else None
        normalized_symbol = symbol.strip().upper() if symbol is not None else None
        parsed_states: tuple[OrderState, ...] = ()
        if state:
            try:
                parsed_states = tuple(OrderState(s) for s in state)
            except ValueError as error:
                raise ToolError(f"invalid_order_state: {error}") from error

        filters = OrderListFilters(
            account_id=account_id,
            provider=normalized_provider,
            symbol=normalized_symbol,
            states=tuple(s.value for s in parsed_states),
            start_date=start,
            end_date=end,
        )
        try:
            after = decode_order_cursor(cursor, filters, limit) if cursor else None
        except ValueError as error:
            raise ToolError("invalid_cursor: Invalid order cursor") from error

        page = repo.list_orders(
            limit=limit,
            after=after,
            account_id=account_id,
            states=parsed_states,
            provider=normalized_provider,
            symbol=normalized_symbol,
            start_date=start,
            end_date=end,
        )

        return format_order_page_response(page, reconciliation, service_clock())

    @mcp.tool()
    async def get_order(order_id: str) -> dict[str, object]:
        """Get details of an existing order by ID.

        Read-only. Does not place or modify orders. Does not expose raw provider
        responses or broker account hashes.
        """
        order = repo.order(order_id)
        if order is None:
            raise ToolError(f"order_not_found: Order '{order_id}' not found")
        return {"order": order.to_dict()}

    return mcp


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ToolError(
            f"invalid_date: {field_name} must be an ISO-8601 date (YYYY-MM-DD)"
        ) from error


mcp = create_server(FixturePortfolioProvider())
