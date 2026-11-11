from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote as encode

from portfolio_mcp.alpaca_market_data import AlpacaMarketDataProvider
from portfolio_mcp.alpaca_portfolio import AlpacaPortfolioProvider, _mapping
from portfolio_mcp.connection_store import ConnectionStore
from portfolio_mcp.stored_orders import OrderDraft
from portfolio_mcp.trading_safety import TradeIntent, TradingGuard
from portfolio_mcp.trading_service import SubmissionCheck, TradingValidationError


class AlpacaTradingPolicy:
    def __init__(
        self,
        repository: ConnectionStore,
        portfolio: AlpacaPortfolioProvider,
        market_data: AlpacaMarketDataProvider,
        guard: TradingGuard,
        clock: Callable[[], datetime] | None = None,
    ):
        self._repository = repository
        self._portfolio = portfolio
        self._market_data = market_data
        self._guard = guard
        self._clock = clock or (lambda: datetime.now(UTC))

    async def __call__(self, draft: OrderDraft, now: datetime) -> SubmissionCheck:
        route = self._repository.account_route(draft.account_id)
        if (
            route is None
            or route.provider_route != "alpaca"
            or not route.active
            or not route.included
            or not route.trade_enabled
            or route.credential_reference != "server_env:alpaca"
        ):
            raise TradingValidationError(
                "account_route_unavailable", "The account is not enabled for trading"
            )
        try:
            account = _mapping(await self._portfolio._get("account"))
            if (
                account.get("id") != route.upstream_account_id
                or account.get("status") != "ACTIVE"
                or account.get("trading_blocked") is not False
                or account.get("account_blocked") is not False
            ):
                raise TradingValidationError(
                    "capability_unavailable",
                    "Alpaca account is unavailable for trading",
                )
            asset = _mapping(
                await self._portfolio._get(f"assets/{encode(draft.symbol, safe='')}")
            )
            if (
                asset.get("symbol") != draft.symbol
                or asset.get("status") != "active"
                or asset.get("class") != "us_equity"
                or asset.get("tradable") is not True
            ):
                raise TradingValidationError(
                    "unsupported_instrument", "This instrument is not tradable"
                )
            intent = TradeIntent(
                draft.account_id,
                draft.symbol,
                draft.asset_class,
                draft.side,
                draft.order_type,
                draft.quantity,
                draft.limit_price,
                draft.quote_bid_price,
                draft.quote_ask_price,
                draft.quote_last_price,
            )
            valid_until = draft.expires_at
            if draft.order_type == "market":
                quote = await self._market_data.get_execution_quote(draft.instrument_id)
                if (
                    quote.instrument.symbol != draft.symbol
                    or quote.feed != "iex"
                    or quote.delay_seconds != 0
                    or not timedelta()
                    <= self._clock() - quote.observed_at
                    <= timedelta(seconds=60)
                ):
                    raise TradingValidationError(
                        "quote_stale", "A current IEX quote is required"
                    )
                valid_until = quote.observed_at + timedelta(seconds=60)
                intent = replace(
                    intent,
                    bid_price=quote.bid_price,
                    ask_price=quote.ask_price,
                    last_price=None,
                )
            decision = self._guard.evaluate(intent)
            if not decision.allowed:
                raise TradingValidationError(
                    decision.violations[0].code, decision.violations[0].message
                )
            if draft.side == "buy":
                buying_power = _positive_amount(account.get("buying_power"))
                cash = _positive_amount(account.get("cash"))
                if (
                    decision.estimated_notional is None
                    or min(cash, buying_power) < decision.estimated_notional
                ):
                    raise TradingValidationError(
                        "insufficient_funds", "Available funds are insufficient"
                    )
            else:
                position = _mapping(
                    await self._portfolio._get(
                        f"positions/{encode(draft.symbol, safe='')}"
                    )
                )
                if (
                    position.get("symbol") != draft.symbol
                    or _positive_amount(position.get("qty_available")) < draft.quantity
                ):
                    raise TradingValidationError(
                        "insufficient_holdings", "Available shares are insufficient"
                    )
            if self._clock() > valid_until or self._clock() > draft.expires_at:
                raise TradingValidationError(
                    "quote_stale", "The trading review has expired"
                )
            return SubmissionCheck(intent, min(valid_until, draft.expires_at))
        except TradingValidationError:
            raise
        except Exception:
            raise TradingValidationError(
                "trading_state_unavailable", "Current trading state is unavailable"
            ) from None


def _positive_amount(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Invalid amount")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid amount") from None
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid amount")
    return result
