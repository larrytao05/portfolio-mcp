import asyncio
import multiprocessing
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from multiprocessing.connection import Connection
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.database import Database
from portfolio_mcp.execution import (
    CapabilityState,
    ExecutionCapability,
    ExecutionCommand,
    ExecutionError,
    ExecutionResult,
    FixtureExecutionProvider,
    OrderState,
    require_transition,
)
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.order_authorization_store import authorization_for_draft
from portfolio_mcp.order_query_store import (
    list_order_events,
    order_draft,
    order_for_draft,
)
from portfolio_mcp.order_query_store import order as load_order
from portfolio_mcp.order_submission_store import (
    begin_order_submission,
    finish_order_submission,
    recover_stranded_submissions,
)
from portfolio_mcp.portfolio_store import save_refresh
from portfolio_mcp.server import create_server
from portfolio_mcp.submission_locks import SubmissionLockError
from portfolio_mcp.trading_safety import (
    TradeIntent,
    TradingGuard,
    TradingSettingsService,
)
from portfolio_mcp.trading_service import (
    OrderDraftService,
    OrderSubmissionService,
    TradingValidationError,
    allow_fixture_submission,
    fixture_submission_validator,
)


def _start_mcp_and_report_order_state(
    database_url: str, draft_id: str, now: datetime, connection: Connection
) -> None:
    database = Database(database_url, lambda: now)
    create_server(
        FixturePortfolioProvider(),
        database_url=database_url,
        clock=lambda: now,
        database=database,
    )
    order = order_for_draft(database, draft_id)
    connection.send(order.state.value if order is not None else None)
    connection.close()


def _submit_and_wait_for_termination(
    database_url: str,
    draft_id: str,
    fingerprint: str,
    now: datetime,
    started: Any,
) -> None:
    database = Database(database_url, lambda: now)

    class BlockingProvider(FixtureExecutionProvider):
        async def submit_order(self, command: ExecutionCommand) -> ExecutionResult:
            self.invocations.append(("submit", command.client_order_id))
            started.set()
            await asyncio.sleep(3600)
            raise AssertionError("the terminated provider call unexpectedly returned")

    service = OrderSubmissionService(
        database, BlockingProvider(), lambda: now, allow_fixture_submission
    )
    asyncio.run(service.confirm(draft_id, fingerprint, True))


def command() -> ExecutionCommand:
    return ExecutionCommand(
        client_order_id="client-1",
        account_id="schwab-taxable-demo",
        provider="Schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        side="buy",
        order_type="limit",
        quantity=Decimal("1"),
        limit_price=Decimal("300"),
    )


def enable_trading_api(client: TestClient) -> None:
    assert client.post("/api/refresh").status_code == 200
    response = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": True,
            "kill_switch_active": False,
            "max_order_shares": "1000",
            "max_order_notional_usd": "1000000",
            "version": 0,
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_trading_guard_applies_conservative_limits_and_capabilities(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    portfolio = FixturePortfolioProvider()
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}", lambda: now)
    snapshots = [
        await portfolio.get_holdings(account.id)
        for account in await portfolio.list_accounts()
    ]
    save_refresh(
        database,
        snapshots,
        now,
        now,
        now.date(),
        capabilities=await portfolio.get_account_capabilities(
            [snapshot.account.id for snapshot in snapshots]
        ),
    )
    settings = TradingSettingsService(database, lambda: now)
    settings.replace(
        live_trading_enabled=True,
        kill_switch_active=False,
        max_order_shares="2",
        max_order_notional_usd="200",
        version=0,
    )
    guard = TradingGuard(database, settings)

    allowed = guard.evaluate(
        TradeIntent(
            account_id="schwab-taxable-demo",
            symbol="VTI",
            asset_class="equity_etf",
            side="buy",
            order_type="market",
            quantity=Decimal("2"),
            limit_price=None,
            bid_price=Decimal("99"),
            ask_price=Decimal("100"),
            last_price=Decimal("98"),
        )
    )
    assert allowed.allowed
    assert allowed.estimated_notional == Decimal("200")

    blocked = guard.evaluate(
        TradeIntent(
            account_id="schwab-taxable-demo",
            symbol="VTI",
            asset_class="etf",
            side="buy",
            order_type="market",
            quantity=Decimal("3"),
            limit_price=None,
            bid_price=Decimal("99"),
            ask_price=Decimal("100"),
            last_price=None,
        )
    )
    assert {violation.code for violation in blocked.violations} == {
        "share_limit_exceeded",
        "notional_limit_exceeded",
    }


@pytest.mark.asyncio
async def test_trading_guard_fails_closed_for_each_safety_precondition(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    portfolio = FixturePortfolioProvider()
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}", lambda: now)
    snapshots = [
        await portfolio.get_holdings(account.id)
        for account in await portfolio.list_accounts()
    ]
    save_refresh(
        database,
        snapshots,
        now,
        now,
        now.date(),
        capabilities=await portfolio.get_account_capabilities(
            [snapshot.account.id for snapshot in snapshots]
        ),
    )
    settings = TradingSettingsService(database, lambda: now)
    enabled = settings.replace(
        live_trading_enabled=True,
        kill_switch_active=False,
        max_order_shares="100",
        max_order_notional_usd="10000",
        version=0,
    )
    guard = TradingGuard(database, settings)
    intent = TradeIntent(
        account_id="schwab-taxable-demo",
        symbol="VTI",
        asset_class="etf",
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        limit_price=None,
        bid_price=Decimal("99"),
        ask_price=Decimal("100"),
        last_price=Decimal("98"),
    )

    assert "capability_unavailable" in _violation_codes(
        guard.evaluate(replace(intent, account_id="fidelity-roth-demo"))
    )
    assert "capability_unsupported" in _violation_codes(
        guard.evaluate(replace(intent, asset_class="crypto"))
    )
    assert "quote_unavailable" in _violation_codes(
        guard.evaluate(replace(intent, bid_price=None, ask_price=None, last_price=None))
    )
    assert "invalid_quantity" in _violation_codes(
        guard.evaluate(replace(intent, quantity=Decimal("1.5")))
    )
    assert "insufficient_holdings" in _violation_codes(
        guard.evaluate(replace(intent, side="sell", quantity=Decimal("10")))
    )

    blocked = settings.replace(
        live_trading_enabled=False,
        kill_switch_active=True,
        max_order_shares="100",
        max_order_notional_usd="10000",
        version=enabled.version,
    )
    assert _violation_codes(guard.evaluate(intent)) == {
        "trading_disabled",
        "kill_switch_active",
    }
    assert blocked.version == 2


def _violation_codes(decision) -> set[str]:
    return {violation.code for violation in decision.violations}


class IndeterminateExecutionProvider:
    def __init__(
        self,
        result: object | BaseException,
        capability: object | None = None,
    ) -> None:
        self.result = result
        self.capability = (
            capability
            if capability is not None
            else ExecutionCapability(
                "schwab-taxable-demo", CapabilityState.SUPPORTED, can_submit=True
            )
        )
        self.invocations = 0

    async def get_execution_capability(self, account_id: str) -> ExecutionCapability:
        if isinstance(self.capability, BaseException):
            raise self.capability
        return cast(ExecutionCapability, self.capability)

    async def submit_order(self, command: ExecutionCommand) -> ExecutionResult:
        del command
        self.invocations += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return cast(ExecutionResult, self.result)

    async def get_order(self, broker_order_id: str) -> ExecutionResult | None:
        del broker_order_id
        return None

    async def list_recent_orders(
        self, client_order_id: str, limit: int
    ) -> list[ExecutionResult]:
        del client_order_id
        del limit
        return []

    async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
        del broker_order_id
        return ExecutionResult(OrderState.UNKNOWN)


async def create_limit_draft(
    database: Database,
    now: datetime,
    account_id: str = "schwab-taxable-demo",
):
    portfolio = FixturePortfolioProvider()
    service = await enabled_order_draft_service(
        database,
        portfolio,
        FixtureMarketDataProvider(),
        lambda: now,
    )
    return await service.create(
        account_id=account_id,
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="300.25",
    )


async def enabled_trading_guard(
    database: Database, portfolio: FixturePortfolioProvider
) -> TradingGuard:
    refreshed_at = datetime.now(UTC)
    accounts = await portfolio.list_accounts()
    snapshots = [await portfolio.get_holdings(account.id) for account in accounts]
    capabilities = [
        replace(
            capability,
            observed_at=refreshed_at,
            last_success_at=refreshed_at,
            is_stale=False,
        )
        for capability in await portfolio.get_account_capabilities(
            [account.id for account in accounts]
        )
    ]
    save_refresh(
        database,
        snapshots,
        refreshed_at,
        refreshed_at,
        refreshed_at.date(),
        capabilities=capabilities,
    )
    settings = TradingSettingsService(database, lambda: refreshed_at)
    settings.replace(
        live_trading_enabled=True,
        kill_switch_active=False,
        max_order_shares="1000",
        max_order_notional_usd="1000000",
        version=0,
    )
    return TradingGuard(database, settings)


async def enabled_order_draft_service(
    database: Database,
    portfolio: FixturePortfolioProvider,
    market_data: FixtureMarketDataProvider,
    clock: Callable[[], datetime],
) -> OrderDraftService:
    return OrderDraftService(
        database,
        market_data,
        clock,
        await enabled_trading_guard(database, portfolio),
    )


@pytest.mark.asyncio
async def test_fixture_execution_provider_records_all_contract_operations() -> None:
    provider = FixtureExecutionProvider("accepted")

    accepted = await provider.submit_order(command())
    assert accepted.state == OrderState.ACCEPTED
    assert accepted.broker_order_id is not None
    assert await provider.get_order(accepted.broker_order_id) == accepted
    assert await provider.list_recent_orders("client-1", 10) == [accepted]
    canceled = await provider.cancel_order(accepted.broker_order_id)
    assert canceled.state == OrderState.CANCELED
    assert [name for name, _ in provider.invocations] == [
        "submit",
        "lookup",
        "recent",
        "cancel",
    ]


@pytest.mark.asyncio
async def test_fixture_recent_order_search_is_bounded_and_validated() -> None:
    provider = FixtureExecutionProvider()
    first = await provider.submit_order(command())
    second = await provider.submit_order(command())

    assert await provider.list_recent_orders("client-1", 1) == [first]
    for invalid_limit in (0, 101):
        with pytest.raises(ExecutionError, match="between 1 and 100"):
            await provider.list_recent_orders("client-1", invalid_limit)
    assert second.state == OrderState.ACCEPTED


@pytest.mark.asyncio
async def test_fixture_capabilities_deny_unknown_accounts_by_default() -> None:
    provider = FixtureExecutionProvider()

    eligible = await provider.get_execution_capability("schwab-taxable-demo")
    unknown = await provider.get_execution_capability("missing-account")

    assert eligible.permits_submission
    assert eligible.permits_cancellation
    assert unknown.state == CapabilityState.UNKNOWN
    assert not unknown.permits_submission
    assert not unknown.permits_cancellation


@pytest.mark.asyncio
async def test_drafts_require_whole_share_quantities(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    service = OrderDraftService(
        Database(f"sqlite:///{tmp_path / 'portfolio.db'}"),
        FixtureMarketDataProvider(),
        lambda: now,
    )

    with pytest.raises(TradingValidationError, match="whole-share"):
        await service.create(
            account_id="schwab-taxable-demo",
            instrument_id="us-etf:VTI",
            side="buy",
            order_type="limit",
            quantity="1.5",
            limit_price="300.25",
        )


@pytest.mark.asyncio
async def test_drafts_accept_canonical_etf_asset_class(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)

    class CanonicalEtfMarketDataProvider(FixtureMarketDataProvider):
        async def get_quote(self, instrument_id: str):
            quote = await super().get_quote(instrument_id)
            return replace(
                quote, instrument=replace(quote.instrument, asset_class="etf")
            )

    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    portfolio = FixturePortfolioProvider()
    service = await enabled_order_draft_service(
        database,
        portfolio,
        CanonicalEtfMarketDataProvider(),
        lambda: now,
    )
    draft = await service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="300.25",
    )

    assert draft.asset_class == "etf"
    assert draft.estimated_notional == Decimal("300.25")
    assert draft.account_refreshed_at is not None
    assert draft.capability_observed_at is not None
    assert order_draft(database, draft.id) == draft


@pytest.mark.asyncio
async def test_drafts_reject_future_market_quotes(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)

    class FutureQuoteProvider(FixtureMarketDataProvider):
        async def get_quote(self, instrument_id: str):
            quote = await super().get_quote(instrument_id)
            return replace(quote, observed_at=now + timedelta(seconds=1))

    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    portfolio = FixturePortfolioProvider()
    service = await enabled_order_draft_service(
        database, portfolio, FutureQuoteProvider(), lambda: now
    )

    with pytest.raises(TradingValidationError, match="current quote"):
        await service.create(
            account_id="schwab-taxable-demo",
            instrument_id="us-etf:VTI",
            side="buy",
            order_type="market",
            quantity="1",
            limit_price=None,
        )


def test_custom_execution_provider_requires_explicit_policy_validator(tmp_path) -> None:
    with pytest.raises(ValueError, match="policy validator"):
        create_app(
            FixturePortfolioProvider(),
            database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
            execution_provider=IndeterminateExecutionProvider(None),
        )


def test_fixture_execution_subclass_requires_explicit_policy_validator(
    tmp_path,
) -> None:
    class CustomFixtureExecutionProvider(FixtureExecutionProvider):
        pass

    with pytest.raises(ValueError, match="policy validator"):
        create_app(
            FixturePortfolioProvider(),
            database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
            execution_provider=CustomFixtureExecutionProvider(),
        )


@pytest.mark.asyncio
async def test_fixture_submission_validator_rechecks_current_sell_holdings(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    portfolio = FixturePortfolioProvider()
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    service = await enabled_order_draft_service(
        database, portfolio, FixtureMarketDataProvider(), lambda: now
    )
    draft = await service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="sell",
        order_type="limit",
        quantity="1",
        limit_price="300",
    )
    portfolio._positions["schwab-taxable-demo"][0] = replace(
        portfolio._positions["schwab-taxable-demo"][0], quantity=Decimal("0")
    )

    with pytest.raises(TradingValidationError, match="current holdings"):
        await fixture_submission_validator(portfolio)(draft, now)


@pytest.mark.asyncio
async def test_fixture_submission_validator_rejects_unavailable_state(tmp_path) -> None:
    class UnavailableFixturePortfolioProvider(FixturePortfolioProvider):
        async def list_accounts(self):
            raise RuntimeError("unavailable")

    draft = await create_limit_draft(
        Database(f"sqlite:///{tmp_path / 'portfolio.db'}"),
        datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
    )

    with pytest.raises(TradingValidationError, match="account state is unavailable"):
        await fixture_submission_validator(UnavailableFixturePortfolioProvider())(
            draft, datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
        )


@pytest.mark.asyncio
async def test_fixture_submission_validator_rejects_missing_account(tmp_path) -> None:
    class MissingFixturePortfolioProvider(FixturePortfolioProvider):
        async def list_accounts(self):
            accounts = await super().list_accounts()
            return accounts[1:]

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    draft = await create_limit_draft(
        Database(f"sqlite:///{tmp_path / 'portfolio.db'}"), now
    )

    with pytest.raises(TradingValidationError, match="Account not found"):
        await fixture_submission_validator(MissingFixturePortfolioProvider())(
            draft, now
        )


@pytest.mark.asyncio
async def test_fixture_submission_validator_rejects_stale_account_state(
    tmp_path,
) -> None:
    class StaleFixturePortfolioProvider(FixturePortfolioProvider):
        async def list_accounts(self):
            accounts = await super().list_accounts()
            return [replace(accounts[0], provider="Different broker"), *accounts[1:]]

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    draft = await create_limit_draft(
        Database(f"sqlite:///{tmp_path / 'portfolio.db'}"), now
    )

    with pytest.raises(TradingValidationError, match="account state is stale"):
        await fixture_submission_validator(StaleFixturePortfolioProvider())(draft, now)


@pytest.mark.asyncio
async def test_market_quote_can_stale_after_intent_and_reject_without_provider_call(
    tmp_path,
) -> None:
    observed_at = FixtureMarketDataProvider.observed_at
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    portfolio = FixturePortfolioProvider()
    draft_service = await enabled_order_draft_service(
        database,
        portfolio,
        FixtureMarketDataProvider(),
        lambda: observed_at,
    )
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="market",
        quantity="1",
        limit_price=None,
    )

    clock_values = iter(
        [
            observed_at + timedelta(seconds=59),
            observed_at + timedelta(seconds=59),
            observed_at + timedelta(seconds=61),
        ]
    )
    provider = FixtureExecutionProvider()
    service = OrderSubmissionService(
        database, provider, lambda: next(clock_values), allow_fixture_submission
    )

    order = await service.confirm(draft.id, draft.fingerprint, True)

    assert order.state == OrderState.REJECTED
    assert order.result_code == "quote_stale"
    assert [name for name, _ in provider.invocations] == ["capability"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "state"),
    [
        ("rejected", OrderState.REJECTED),
        ("partial_fill", OrderState.PARTIALLY_FILLED),
        ("filled", OrderState.FILLED),
    ],
)
async def test_fixture_execution_provider_has_deterministic_lifecycle_scenarios(
    scenario: str, state: OrderState
) -> None:
    result = await FixtureExecutionProvider(scenario).submit_order(command())

    assert result.state == state


def test_state_machine_rejects_terminal_and_regressive_transitions() -> None:
    require_transition(OrderState.SUBMITTING, OrderState.ACCEPTED)
    require_transition(OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED)
    require_transition(OrderState.UNKNOWN, OrderState.FILLED)
    with pytest.raises(ExecutionError):
        require_transition(OrderState.FILLED, OrderState.ACCEPTED)
    with pytest.raises(ExecutionError):
        require_transition(OrderState.ACCEPTED, OrderState.SUBMITTING)


@pytest.mark.asyncio
async def test_repository_enforces_persisted_state_transitions(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    order, _ = begin_order_submission(database, draft, now)
    unknown = finish_order_submission(
        database, order.id, OrderState.UNKNOWN, now, expected_version=order.version
    )
    reconciled = finish_order_submission(
        database, order.id, OrderState.FILLED, now, expected_version=unknown.version
    )

    assert reconciled.state == OrderState.FILLED
    with pytest.raises(ValueError):
        finish_order_submission(
            database,
            order.id,
            OrderState.ACCEPTED,
            now,
            expected_version=reconciled.version,
        )


@pytest.mark.asyncio
async def test_submission_persists_intent_once_and_never_retries_unknown(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, 30, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    portfolio = FixturePortfolioProvider()
    draft_service = await enabled_order_draft_service(
        database,
        portfolio,
        FixtureMarketDataProvider(),
        lambda: now,
    )
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="300",
    )
    provider = FixtureExecutionProvider("timeout")
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )

    first = await service.confirm(draft.id, draft.fingerprint, True)
    replay = await service.confirm(draft.id, draft.fingerprint, True)

    assert first.state == OrderState.UNKNOWN
    assert replay == first
    assert [entry for entry in provider.invocations if entry[0] == "submit"] == [
        ("submit", first.client_order_id)
    ]


@pytest.mark.asyncio
async def test_submission_runs_the_final_policy_check_before_persisting_intent(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    portfolio = FixturePortfolioProvider()
    draft_service = await enabled_order_draft_service(
        database,
        portfolio,
        FixtureMarketDataProvider(),
        lambda: now,
    )
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="300",
    )
    provider = FixtureExecutionProvider()

    async def reject_policy(_, __):
        raise TradingValidationError("trading_disabled", "Trading is disabled")

    service = OrderSubmissionService(database, provider, lambda: now, reject_policy)
    with pytest.raises(TradingValidationError, match="disabled"):
        await service.confirm(draft.id, draft.fingerprint, True)

    assert provider.invocations == []
    assert order_for_draft(database, draft.id) is None
    assert authorization_for_draft(database, draft.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    [
        ExecutionCapability("schwab-taxable-demo", CapabilityState.UNKNOWN),
        ExecutionCapability("schwab-taxable-demo", CapabilityState.UNSUPPORTED),
    ],
)
async def test_unsupported_execution_capability_blocks_before_intent(
    tmp_path, capability: ExecutionCapability
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    provider = FixtureExecutionProvider(capabilities={draft.account_id: capability})
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )

    with pytest.raises(TradingValidationError, match="cannot submit"):
        await service.confirm(draft.id, draft.fingerprint, True)

    assert [name for name, _ in provider.invocations] == ["capability"]
    assert order_for_draft(database, draft.id) is None
    assert authorization_for_draft(database, draft.id) is None


@pytest.mark.asyncio
async def test_capability_read_failure_blocks_before_intent(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    provider = IndeterminateExecutionProvider(
        ExecutionResult(OrderState.ACCEPTED, "fixture-order"),
        RuntimeError("unavailable"),
    )
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )

    with pytest.raises(TradingValidationError, match="capability is unavailable"):
        await service.confirm(draft.id, draft.fingerprint, True)

    assert provider.invocations == 0
    assert order_for_draft(database, draft.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    [
        object(),
        ExecutionCapability(
            "schwab-taxable-demo", cast(CapabilityState, "SUPPORTED"), can_submit=True
        ),
    ],
)
async def test_malformed_capability_blocks_before_intent(
    tmp_path, capability: object
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    provider = IndeterminateExecutionProvider(
        ExecutionResult(OrderState.ACCEPTED, "fixture-order"), capability
    )
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )

    with pytest.raises(TradingValidationError, match="capability is unavailable"):
        await service.confirm(draft.id, draft.fingerprint, True)

    assert provider.invocations == 0
    assert order_for_draft(database, draft.id) is None


@pytest.mark.asyncio
async def test_mismatched_capability_account_blocks_before_intent(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    provider = IndeterminateExecutionProvider(
        ExecutionResult(OrderState.ACCEPTED, "fixture-order"),
        ExecutionCapability(
            "another-account", CapabilityState.SUPPORTED, can_submit=True
        ),
    )
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )

    with pytest.raises(TradingValidationError, match="capability is unavailable"):
        await service.confirm(draft.id, draft.fingerprint, True)

    assert provider.invocations == 0
    assert order_for_draft(database, draft.id) is None


@pytest.mark.asyncio
async def test_empty_capability_map_denies_default_account() -> None:
    provider = FixtureExecutionProvider(capabilities={})

    capability = await provider.get_execution_capability("schwab-taxable-demo")

    assert capability.state == CapabilityState.UNKNOWN
    assert not capability.permits_submission


def test_confirm_api_blocks_unknown_account_capability_before_submit(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    execution = FixtureExecutionProvider(capabilities={})
    client = TestClient(
        create_app(
            FixturePortfolioProvider(),
            execution_provider=execution,
            database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
            clock=lambda: now,
        )
    )
    enable_trading_api(client)
    draft = client.post(
        "/api/order-drafts",
        json={
            "account_id": "schwab-taxable-demo",
            "instrument_id": "us-etf:VTI",
            "side": "buy",
            "order_type": "limit",
            "quantity": "1",
            "limit_price": "300.25",
        },
    ).json()["draft"]

    response = client.post(
        f"/api/order-drafts/{draft['id']}/confirm",
        json={"expected_fingerprint": draft["fingerprint"], "confirmed": True},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "execution_unsupported"
    assert [name for name, _ in execution.invocations] == ["capability"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        "ACCEPTED",
        ExecutionResult(OrderState.ACCEPTED, None),
        ExecutionResult(OrderState.ACCEPTED, ""),
        ExecutionResult(OrderState.ACCEPTED, "   "),
        ExecutionResult(OrderState.ACCEPTED, cast(str, 42)),
    ],
)
async def test_malformed_or_missing_identity_response_becomes_unknown(
    tmp_path, result: object
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    provider = IndeterminateExecutionProvider(result)
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )

    order = await service.confirm(draft.id, draft.fingerprint, True)

    assert order.state == OrderState.UNKNOWN
    assert provider.invocations == 1


@pytest.mark.asyncio
async def test_cancellation_persists_unknown_then_propagates_cancellation(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    provider = IndeterminateExecutionProvider(asyncio.CancelledError())
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )

    with pytest.raises(asyncio.CancelledError):
        await service.confirm(draft.id, draft.fingerprint, True)

    recovered = order_for_draft(database, draft.id)
    assert recovered is not None
    assert recovered.state == OrderState.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["partial_fill", "filled"])
async def test_definite_immediate_fill_results_are_preserved(
    tmp_path, scenario: str
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    service = OrderSubmissionService(
        database,
        FixtureExecutionProvider(scenario),
        lambda: now,
        allow_fixture_submission,
    )

    order = await service.confirm(draft.id, draft.fingerprint, True)

    assert (
        order.state
        == {
            "partial_fill": OrderState.PARTIALLY_FILLED,
            "filled": OrderState.FILLED,
        }[scenario]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_result",
    [ExecutionResult(OrderState.ACCEPTED, "fixture-order"), TimeoutError()],
)
async def test_submission_result_event_uses_provider_observation_time(
    tmp_path, provider_result: object
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    clock = [now]
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}", lambda: clock[0])
    draft = await create_limit_draft(database, now)

    class DelayedProvider(IndeterminateExecutionProvider):
        async def submit_order(self, command: ExecutionCommand) -> ExecutionResult:
            del command
            self.invocations += 1
            clock[0] += timedelta(minutes=1)
            if isinstance(self.result, BaseException):
                raise self.result
            return cast(ExecutionResult, self.result)

    service = OrderSubmissionService(
        database,
        DelayedProvider(provider_result),
        lambda: clock[0],
        allow_fixture_submission,
    )

    order = await service.confirm(draft.id, draft.fingerprint, True)

    events = list_order_events(database, order_id=order.id, limit=100).items
    submitted_at = next(
        event.occurred_at
        for event in events
        if event.event_type.value == "submission_started"
    )
    result_at = next(
        event.occurred_at
        for event in events
        if event.event_type.value == "submission_result"
    )
    assert result_at > submitted_at
    stored = load_order(database, order.id)
    assert stored is not None
    assert stored.provider_submission_started_at == submitted_at


@pytest.mark.asyncio
async def test_startup_recovers_stranded_submitting_order_without_resubmission(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    pending, created = begin_order_submission(database, draft, now)
    assert created
    provider = FixtureExecutionProvider()
    create_server(
        FixturePortfolioProvider(),
        execution_provider=provider,
        database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
        clock=lambda: now,
        database=database,
    )

    order = order_for_draft(database, draft.id)

    assert order is not None
    assert order.id == pending.id
    assert order.state == OrderState.UNKNOWN
    assert provider.invocations == []


@pytest.mark.asyncio
async def test_api_order_list_recovers_submission_after_app_startup(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    database = Database(database_url, lambda: now)
    draft = await create_limit_draft(database, now)
    provider = FixtureExecutionProvider()
    app = create_app(
        FixturePortfolioProvider(),
        execution_provider=provider,
        database_url=database_url,
        clock=lambda: now,
    )
    pending, created = begin_order_submission(database, draft, now)
    assert created

    response = TestClient(app).get("/api/orders")

    assert response.status_code == 200
    assert response.json()["orders"][0]["id"] == pending.id
    assert response.json()["orders"][0]["state"] == OrderState.UNKNOWN.value
    assert provider.invocations == []


@pytest.mark.asyncio
async def test_mcp_startup_does_not_recover_live_submission(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    database = Database(database_url, lambda: now)
    draft = await create_limit_draft(database, now)
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingProvider(FixtureExecutionProvider):
        async def submit_order(self, command: ExecutionCommand) -> ExecutionResult:
            self.invocations.append(("submit", command.client_order_id))
            entered.set()
            await release.wait()
            return ExecutionResult(
                OrderState.ACCEPTED, f"fixture-{command.client_order_id}"
            )

    provider = BlockingProvider()
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )
    submission = asyncio.create_task(service.confirm(draft.id, draft.fingerprint, True))
    await entered.wait()

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_start_mcp_and_report_order_state,
        args=(database_url, draft.id, now, send),
    )
    process.start()
    send.close()
    process_state = None
    got_process_state = False
    try:
        got_process_state = receive.poll(30)
        if got_process_state:
            process_state = receive.recv()
        process.join(timeout=30)
    finally:
        receive.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=30)
        release.set()
    completed = await submission

    assert got_process_state, "MCP process did not finish startup recovery"
    assert process.exitcode == 0
    assert process_state == OrderState.SUBMITTING.value
    assert completed.state == OrderState.ACCEPTED
    assert completed.broker_order_id == f"fixture-{completed.client_order_id}"
    assert [name for name, _ in provider.invocations].count("submit") == 1


@pytest.mark.asyncio
async def test_dead_submission_process_is_recovered_without_retry(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    database = Database(database_url, lambda: now)
    draft = await create_limit_draft(database, now)
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    process = context.Process(
        target=_submit_and_wait_for_termination,
        args=(database_url, draft.id, draft.fingerprint, now, started),
    )
    process.start()
    try:
        assert started.wait(30), "provider write did not start"
        pending = order_for_draft(database, draft.id)
        assert pending is not None
        assert pending.state == OrderState.SUBMITTING
    finally:
        process.terminate()
        process.join(timeout=30)

    assert process.exitcode is not None
    assert recover_stranded_submissions(database, now) == 1
    recovered = order_for_draft(database, draft.id)
    assert recovered is not None
    assert recovered.state == OrderState.UNKNOWN
    assert recover_stranded_submissions(database, now) == 0

    retry_provider = FixtureExecutionProvider()
    service = OrderSubmissionService(
        database, retry_provider, lambda: now, allow_fixture_submission
    )
    result = await service.confirm(draft.id, draft.fingerprint, True)

    assert result.state == OrderState.UNKNOWN
    assert retry_provider.invocations == []


@pytest.mark.asyncio
async def test_concurrent_confirmations_create_one_order_and_authorization(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingProvider(FixtureExecutionProvider):
        async def submit_order(self, command: ExecutionCommand) -> ExecutionResult:
            self.invocations.append(("submit", command.client_order_id))
            started.set()
            await release.wait()
            return ExecutionResult(
                OrderState.ACCEPTED, f"fixture-{command.client_order_id}"
            )

    provider = BlockingProvider()
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )
    first = asyncio.create_task(service.confirm(draft.id, draft.fingerprint, True))
    await started.wait()
    with pytest.raises(TradingValidationError) as error:
        await service.confirm(draft.id, draft.fingerprint, True)
    assert error.value.code == "submission_in_progress"
    release.set()
    completed = await first

    assert completed.state == OrderState.ACCEPTED
    assert [entry for entry in provider.invocations if entry[0] == "submit"] == [
        ("submit", completed.client_order_id)
    ]
    authorization = authorization_for_draft(database, draft.id)
    assert authorization is not None
    assert authorization.expected_fingerprint == draft.fingerprint
    assert authorization.account_id == draft.account_id
    assert authorization.actor == "dashboard-owner"
    events = list_order_events(database, order_id=completed.id, limit=100).items
    assert not any(event.event_type.value == "authorization_failed" for event in events)


@pytest.mark.asyncio
async def test_submission_lock_failure_fails_closed_without_auth_failure_audit(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    provider = FixtureExecutionProvider()
    service = OrderSubmissionService(
        database, provider, lambda: now, allow_fixture_submission
    )

    def fail_claim(draft_id: str):
        del draft_id
        raise SubmissionLockError("permission denied")

    monkeypatch.setattr(database.submission_locks, "claim", fail_claim)
    with pytest.raises(TradingValidationError) as error:
        await service.confirm(draft.id, draft.fingerprint, True)

    assert error.value.code == "submission_lock_unavailable"
    assert provider.invocations == []
    events = list_order_events(database, draft_id=draft.id, limit=100).items
    assert not any(event.event_type.value == "authorization_failed" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("seconds", "allowed"),
    [(59, True), (60, True), (61, False)],
)
async def test_market_confirmation_enforces_quote_freshness_boundary(
    tmp_path, seconds: int, allowed: bool
) -> None:
    observed_at = FixtureMarketDataProvider.observed_at
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    portfolio = FixturePortfolioProvider()
    draft_service = await enabled_order_draft_service(
        database,
        portfolio,
        FixtureMarketDataProvider(),
        lambda: observed_at,
    )
    draft = await draft_service.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="market",
        quantity="1",
        limit_price=None,
    )
    provider = FixtureExecutionProvider()
    service = OrderSubmissionService(
        database,
        provider,
        lambda: observed_at + timedelta(seconds=seconds),
        allow_fixture_submission,
    )

    if allowed:
        assert (await service.confirm(draft.id, draft.fingerprint, True)).state == (
            OrderState.ACCEPTED
        )
        assert [name for name, _ in provider.invocations] == ["capability", "submit"]
    else:
        with pytest.raises(TradingValidationError, match="stale"):
            await service.confirm(draft.id, draft.fingerprint, True)
        assert [name for name, _ in provider.invocations] == ["capability"]


def test_confirm_api_requires_explicit_confirmation_and_replays_safely(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    provider = FixtureExecutionProvider()
    client = TestClient(
        create_app(
            FixturePortfolioProvider(),
            market_data_provider=FixtureMarketDataProvider(),
            execution_provider=provider,
            database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
            clock=lambda: now,
        )
    )
    enable_trading_api(client)
    draft_response = client.post(
        "/api/order-drafts",
        json={
            "account_id": "schwab-taxable-demo",
            "instrument_id": "us-etf:VTI",
            "side": "buy",
            "order_type": "limit",
            "quantity": "1",
            "limit_price": "300",
        },
    )
    assert draft_response.status_code == 200
    draft = draft_response.json()["draft"]

    no_confirmation = client.post(
        f"/api/order-drafts/{draft['id']}/confirm",
        json={"expected_fingerprint": draft["fingerprint"], "confirmed": False},
    )
    assert no_confirmation.status_code == 422
    assert no_confirmation.json()["error"]["code"] == "confirmation_required"
    assert provider.invocations == []

    first = client.post(
        f"/api/order-drafts/{draft['id']}/confirm",
        json={"expected_fingerprint": draft["fingerprint"], "confirmed": True},
    )
    replay = client.post(
        f"/api/order-drafts/{draft['id']}/confirm",
        json={"expected_fingerprint": draft["fingerprint"], "confirmed": True},
    )
    wrong_confirmation = client.post(
        f"/api/order-drafts/{draft['id']}/confirm",
        json={"expected_fingerprint": draft["fingerprint"], "confirmed": False},
    )
    wrong_fingerprint = client.post(
        f"/api/order-drafts/{draft['id']}/confirm",
        json={"expected_fingerprint": "wrong", "confirmed": True},
    )
    assert first.status_code == 200
    assert replay.json()["order"]["id"] == first.json()["order"]["id"]
    assert wrong_confirmation.status_code == 422
    assert wrong_confirmation.json()["error"]["code"] == "confirmation_required"
    assert wrong_fingerprint.status_code == 422
    assert wrong_fingerprint.json()["error"]["code"] == "draft_changed"
    assert [name for name, _ in provider.invocations] == ["capability", "submit"]


def test_kill_switch_blocks_submission_after_a_draft_is_reviewed(tmp_path) -> None:
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    provider = FixtureExecutionProvider()
    client = TestClient(
        create_app(
            FixturePortfolioProvider(),
            execution_provider=provider,
            database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
            clock=lambda: now,
        )
    )
    enable_trading_api(client)
    draft = client.post(
        "/api/order-drafts",
        json={
            "account_id": "schwab-taxable-demo",
            "instrument_id": "us-etf:VTI",
            "side": "buy",
            "order_type": "limit",
            "quantity": "1",
            "limit_price": "300",
        },
    ).json()["draft"]
    response = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": True,
            "kill_switch_active": True,
            "max_order_shares": "1000",
            "max_order_notional_usd": "1000000",
            "version": 1,
        },
    )
    assert response.status_code == 200

    confirmation = client.post(
        f"/api/order-drafts/{draft['id']}/confirm",
        json={"expected_fingerprint": draft["fingerprint"], "confirmed": True},
    )

    assert confirmation.status_code == 200
    assert confirmation.json()["order"]["result"]["code"] == "kill_switch_active"
    assert [name for name, _ in provider.invocations] == ["capability"]
