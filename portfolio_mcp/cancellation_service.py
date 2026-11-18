import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from portfolio_mcp.connection_store import ConnectionStore
from portfolio_mcp.execution import ExecutionProvider, OrderState
from portfolio_mcp.trading_service import TradingValidationError


class CancellationService:
    def __init__(
        self,
        repository: ConnectionStore,
        provider_for_account: Callable[[str], ExecutionProvider],
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._provider_for_account = provider_for_account
        self._clock = clock

    def _require_route(self, account_id: str) -> None:
        route = self._repository.account_route(account_id)
        if (
            route is None
            or not route.active
            or not route.included
            or not route.trade_enabled
        ):
            raise TradingValidationError(
                "account_route_unavailable", "The account is not enabled for trading"
            )

    async def cancel(
        self, account_id: str, broker_order_id: str, confirmed: bool
    ) -> str:
        if not confirmed:
            raise TradingValidationError(
                "confirmation_required", "Explicit confirmation is required"
            )
        self._require_route(account_id)
        existing = self._repository.cancellation_state(account_id, broker_order_id)
        if existing is not None:
            return existing
        provider = self._provider_for_account(account_id)
        capability = await provider.get_execution_capability(account_id)
        if capability.account_id != account_id or not capability.permits_cancellation:
            raise TradingValidationError(
                "capability_unavailable", "Cancellation is unavailable"
            )
        order = await provider.get_order(broker_order_id)
        if (
            order is None
            or order.broker_order_id != broker_order_id
            or order.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
        ):
            raise TradingValidationError(
                "order_unavailable", "The order is not available for cancellation"
            )
        try:
            state, created = await asyncio.to_thread(
                self._repository.reserve_cancellation,
                account_id,
                broker_order_id,
                self._now(),
            )
        except ValueError:
            raise TradingValidationError(
                "reconciliation_required", "An earlier action requires reconciliation"
            ) from None
        if not created:
            return state
        self._require_route(account_id)
        try:
            result = await provider.cancel_order(broker_order_id)
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self._repository.finish_cancellation,
                account_id,
                broker_order_id,
                "UNKNOWN",
                self._now(),
            )
            raise
        except Exception:
            result = None
        state = (
            result.state.value
            if result is not None
            and result.broker_order_id == broker_order_id
            and result.state
            in {OrderState.CANCEL_PENDING, OrderState.CANCELED, OrderState.FILLED}
            else "UNKNOWN"
        )
        await asyncio.to_thread(
            self._repository.finish_cancellation,
            account_id,
            broker_order_id,
            state,
            self._now(),
        )
        return state

    async def reconcile(self, account_id: str, broker_order_id: str) -> str:
        self._require_route(account_id)
        state = self._repository.cancellation_state(account_id, broker_order_id)
        if state is None:
            raise TradingValidationError(
                "cancellation_not_found", "Cancellation not found"
            )
        if state not in {"UNKNOWN", "CANCEL_PENDING"}:
            return state
        provider = self._provider_for_account(account_id)
        try:
            capability = await provider.get_execution_capability(account_id)
            result = (
                await provider.get_order(broker_order_id)
                if capability.account_id == account_id
                and capability.permits_cancellation
                else None
            )
        except Exception:
            result = None
        if (
            result is not None
            and result.broker_order_id == broker_order_id
            and result.state in {OrderState.CANCELED, OrderState.FILLED}
        ):
            state = result.state.value
        await asyncio.to_thread(
            self._repository.finish_cancellation,
            account_id,
            broker_order_id,
            state,
            self._now(),
            kind="reconciliation",
        )
        return state

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Clock must be timezone-aware")
        return value.astimezone(UTC)
