import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from portfolio_mcp.cancellation_service import CancellationService
from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.execution import (
    CapabilityState,
    ExecutionCapability,
    ExecutionIndeterminateError,
    ExecutionResult,
    OrderState,
)
from portfolio_mcp.trading_service import TradingValidationError


class Provider:
    def __init__(self):
        self.state = OrderState.ACCEPTED
        self.calls = 0
        self.timeout = True

    async def get_execution_capability(self, account_id):
        return ExecutionCapability(account_id, CapabilityState.SUPPORTED, True, True)

    async def get_order(self, order_id):
        return ExecutionResult(self.state, order_id)

    async def cancel_order(self, order_id):
        self.calls += 1
        if self.timeout:
            raise ExecutionIndeterminateError("unknown")
        return ExecutionResult(OrderState.CANCEL_PENDING, order_id)


def setup(tmp_path):
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'db.sqlite'}")
    repo.save_connection("c", "alpaca", "live", "server_env:alpaca", True)
    repo.save_account_route("a", "c", "upstream", trade_enabled=True)
    provider = Provider()
    service = CancellationService(
        repo, lambda _: cast(Any, provider), lambda: datetime(2026, 1, 1, tzinfo=UTC)
    )
    return repo, provider, service


@pytest.mark.asyncio
async def test_unknown_cancel_does_not_retry_and_blocks_until_terminal(tmp_path):
    repo, provider, service = setup(tmp_path)
    assert await service.cancel("a", "broker-order", True) == "UNKNOWN"
    assert await service.cancel("a", "broker-order", True) == "UNKNOWN"
    assert provider.calls == 1 and repo.has_unresolved_cancellations("a")
    assert await service.reconcile("a", "broker-order") == "UNKNOWN"
    provider.state = OrderState.FILLED
    assert await service.reconcile("a", "broker-order") == "FILLED"
    assert not repo.has_unresolved_cancellations("a")


@pytest.mark.asyncio
async def test_pending_cancel_requires_explicit_confirmation_and_enabled_route(
    tmp_path,
):
    repo, provider, service = setup(tmp_path)
    with pytest.raises(TradingValidationError):
        await service.cancel("a", "one", False)
    provider.timeout = False
    assert await service.cancel("a", "one", True) == "CANCEL_PENDING"
    assert repo.has_unresolved_cancellations("a")
    assert provider.calls == 1
    repo.disconnect_connection("c")
    with pytest.raises(TradingValidationError):
        await service.cancel("a", "two", True)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_concurrent_confirmations_send_at_most_one_cancel(tmp_path):
    _, provider, service = setup(tmp_path)
    results = await asyncio.gather(
        service.cancel("a", "one", True), service.cancel("a", "one", True)
    )
    assert results == ["UNKNOWN", "UNKNOWN"]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_fixture_broker_history_order_can_be_canceled(tmp_path):
    from portfolio_mcp.execution import FixtureExecutionProvider

    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'fixture.sqlite'}")
    repo.save_connection("fixture", "fixture", "fixture", "fixture", True)
    repo.save_account_route(
        "schwab-taxable-demo", "fixture", "schwab-taxable-demo", trade_enabled=True
    )
    provider = FixtureExecutionProvider()
    service = CancellationService(
        repo, lambda _: provider, lambda: datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert (
        await service.cancel("schwab-taxable-demo", "fixture-open", True) == "CANCELED"
    )
    assert (
        await service.cancel("schwab-taxable-demo", "fixture-open", True) == "CANCELED"
    )
    assert [name for name, _ in provider.invocations].count("cancel") == 1
