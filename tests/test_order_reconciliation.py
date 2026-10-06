from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from portfolio_mcp.database import Database
from portfolio_mcp.execution import (
    BrokerOrderSearch,
    BrokerOrderSnapshot,
    ExecutionCommand,
    FillSummary,
    OrderState,
)
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.order_history import OrderStatusSource
from portfolio_mcp.order_query_store import list_order_events
from portfolio_mcp.order_query_store import order as load_order
from portfolio_mcp.order_submission_store import (
    begin_order_submission,
    finish_order_submission,
    mark_provider_submission_started,
)
from portfolio_mcp.portfolio_store import save_refresh
from portfolio_mcp.reconciliation_store import finish_order_reconciliation
from portfolio_mcp.trading_safety import TradingGuard, TradingSettingsService
from portfolio_mcp.trading_service import OrderDraftService


async def create_limit_draft(database: Database, now: datetime):
    refreshed_at = datetime.now(UTC)
    portfolio = FixturePortfolioProvider()
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
    drafts = OrderDraftService(
        database,
        FixtureMarketDataProvider(),
        lambda: now,
        TradingGuard(database, settings),
    )
    return await drafts.create(
        account_id="schwab-taxable-demo",
        instrument_id="us-etf:VTI",
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="300.25",
    )


class ReconciliationFixtureProvider:
    def __init__(
        self,
        search_result: BrokerOrderSearch | BrokerOrderSnapshot | BaseException | None,
        *,
        client_lookup: bool = True,
    ) -> None:
        self.search_result = search_result
        self.supports_client_order_id_lookup = client_lookup
        self.reads: list[tuple[object, ...]] = []
        self.writes: list[str] = []

    async def find_by_broker_order_id(
        self, account_id: str, broker_order_id: str
    ) -> BrokerOrderSnapshot | None:
        self.reads.append(("broker", account_id, broker_order_id))
        if isinstance(self.search_result, BaseException):
            raise self.search_result
        return cast(BrokerOrderSnapshot | None, self.search_result)

    async def find_by_client_order_id(
        self, account_id: str, client_order_id: str
    ) -> BrokerOrderSearch:
        self.reads.append(("client", account_id, client_order_id))
        if isinstance(self.search_result, BaseException):
            raise self.search_result
        return cast(BrokerOrderSearch, self.search_result)

    async def search_orders(
        self, account_id: str, start_at: datetime, end_at: datetime
    ) -> BrokerOrderSearch:
        self.reads.append(("recent", account_id, start_at, end_at))
        if isinstance(self.search_result, BaseException):
            raise self.search_result
        return cast(BrokerOrderSearch, self.search_result)

    async def submit_order(self, command: ExecutionCommand):
        del command
        self.writes.append("submit")
        raise AssertionError("reconciliation must not submit orders")

    async def cancel_order(self, broker_order_id: str):
        del broker_order_id
        self.writes.append("cancel")
        raise AssertionError("reconciliation must not cancel orders")


async def create_unknown_order(database: Database, now: datetime):
    draft = await create_limit_draft(database, now)
    order, created = begin_order_submission(database, draft, now)
    assert created
    order = mark_provider_submission_started(
        database, order.id, expected_version=order.version, started_at=now
    )
    return finish_order_submission(
        database,
        order.id,
        OrderState.UNKNOWN,
        now,
        expected_version=order.version,
        result_code="unknown",
    )


def reconciliation_snapshot(order, *, state: OrderState = OrderState.ACCEPTED):
    from portfolio_mcp.execution import BrokerOrderSnapshot

    return BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=order.client_order_id,
        account_id=order.account_id,
        instrument_id=order.instrument_id,
        side=order.side,
        order_type=order.order_type,
        quantity=order.quantity,
        limit_price=order.limit_price,
        time_in_force="day",
        submitted_at=order.provider_submission_started_at,
        state=state,
        updated_at=order.provider_submission_started_at + timedelta(seconds=2),
        status_label="OPEN",
        fill=None,
    )


@pytest.mark.asyncio
async def test_unknown_reconciliation_resolves_only_one_exact_complete_match(
    tmp_path,
) -> None:
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    order = await create_unknown_order(database, now)
    snapshot = reconciliation_snapshot(order)
    provider = ReconciliationFixtureProvider(BrokerOrderSearch((snapshot,), True))
    service = OrderReconciliationService(database, provider, lambda: now)

    reconciled = await service.reconcile_unknown(order.id)

    assert reconciled.state == OrderState.ACCEPTED, reconciled.result_code
    assert reconciled.broker_order_id == "broker-1"
    assert reconciled.result_source is not None
    assert reconciled.result_source.value == "provider"
    assert reconciled.to_dict()["remaining_quantity"] is None
    assert reconciled.provider_status_label == "OPEN"
    assert provider.writes == []
    assert [read[0] for read in provider.reads] == ["client"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_count", "complete", "expected_code"),
    [(0, True, "not_found"), (2, True, "ambiguous"), (1, False, "incomplete")],
)
async def test_unknown_reconciliation_keeps_unknown_without_unique_complete_match(
    tmp_path, candidate_count: int, complete: bool, expected_code: str
) -> None:
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    order = await create_unknown_order(database, now)
    snapshots = tuple(
        replace(
            reconciliation_snapshot(order),
            broker_order_id=f"broker-{index}",
        )
        for index in range(candidate_count)
    )
    provider = ReconciliationFixtureProvider(BrokerOrderSearch(snapshots, complete))
    service = OrderReconciliationService(database, provider, lambda: now)

    reconciled = await service.reconcile_unknown(order.id)

    assert reconciled.state == OrderState.UNKNOWN
    assert reconciled.broker_order_id is None
    assert reconciled.result_code == expected_code
    assert provider.writes == []


@pytest.mark.asyncio
async def test_recent_fallback_uses_provider_call_window_and_persisted_throttle(
    tmp_path,
) -> None:
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    database = Database(database_url)
    order = await create_unknown_order(database, now)
    started_at = order.provider_submission_started_at
    assert started_at is not None
    snapshot = reconciliation_snapshot(order)
    provider = ReconciliationFixtureProvider(
        BrokerOrderSearch((snapshot,), True), client_lookup=False
    )
    service = OrderReconciliationService(database, provider, lambda: now)

    reconciled = await service.reconcile_unknown(order.id)
    reopened = Database(database_url)
    persisted = load_order(reopened, order.id)
    assert persisted is not None
    assert persisted.state == OrderState.ACCEPTED
    assert persisted.broker_order_id == "broker-1"
    persisted_events = list_order_events(reopened, order_id=order.id, limit=100).items
    assert {event.event_type.value for event in persisted_events} >= {
        "reconciliation_attempted",
        "reconciliation_result",
        "status_transition",
    }
    throttled_provider = ReconciliationFixtureProvider(
        BrokerOrderSearch((), True), client_lookup=False
    )
    throttled = await OrderReconciliationService(
        reopened, throttled_provider, lambda: now
    ).sync(order.id)

    assert reconciled.state == OrderState.ACCEPTED
    assert provider.reads == [
        (
            "recent",
            order.account_id,
            started_at - timedelta(seconds=30),
            started_at + timedelta(minutes=2),
        )
    ]
    assert throttled.state == OrderState.ACCEPTED
    assert throttled_provider.reads == []


@pytest.mark.asyncio
async def test_poll_gate_allows_next_read_at_the_interval_boundary(tmp_path) -> None:
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    database = Database(database_url)
    order = await create_unknown_order(database, now)
    snapshot = reconciliation_snapshot(order)
    first_provider = ReconciliationFixtureProvider(BrokerOrderSearch((snapshot,), True))
    await OrderReconciliationService(
        database, first_provider, lambda: now
    ).reconcile_unknown(order.id)
    boundary_provider = ReconciliationFixtureProvider(
        BrokerOrderSearch((snapshot,), True)
    )

    await OrderReconciliationService(
        Database(database_url),
        boundary_provider,
        lambda: now + timedelta(seconds=30),
    ).sync(order.id)

    assert len(boundary_provider.reads) == 1


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_and_does_not_change_order(
    tmp_path,
) -> None:
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    order = await create_unknown_order(database, now)
    provider = ReconciliationFixtureProvider(
        RuntimeError("secret account token must not be stored")
    )
    reconciled = await OrderReconciliationService(
        database, provider, lambda: now
    ).reconcile_unknown(order.id)

    assert reconciled.state == OrderState.UNKNOWN
    assert reconciled.broker_order_id is None
    assert "secret" not in (reconciled.result_message or "")
    assert provider.writes == []


@pytest.mark.asyncio
async def test_recent_fallback_rejects_identity_mismatch_and_window_edges(
    tmp_path,
) -> None:
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    for index, (offset, matches, identity_mismatch) in enumerate(
        [
            (-30, True, False),
            (120, True, False),
            (-31, False, False),
            (121, False, False),
            (0, False, True),
        ]
    ):
        database = Database(f"sqlite:///{tmp_path / f'portfolio-{index}.db'}")
        order = await create_unknown_order(database, now)
        started_at = order.provider_submission_started_at
        assert started_at is not None
        snapshot = replace(
            reconciliation_snapshot(order),
            submitted_at=started_at + timedelta(seconds=offset),
            side="sell" if identity_mismatch else order.side,
        )
        provider = ReconciliationFixtureProvider(
            BrokerOrderSearch((snapshot,), True), client_lookup=False
        )
        reconciled = await OrderReconciliationService(
            database, provider, lambda: now
        ).reconcile_unknown(order.id)

        if matches:
            assert reconciled.state == OrderState.ACCEPTED
        else:
            assert reconciled.state == OrderState.UNKNOWN


@pytest.mark.asyncio
async def test_reconciliation_advances_fill_metadata_and_expiry_safely(
    tmp_path,
) -> None:
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    order = await create_unknown_order(database, now)
    partial = replace(
        reconciliation_snapshot(order, state=OrderState.PARTIALLY_FILLED),
        fill=FillSummary(Decimal("0.25"), Decimal("300.25")),
        status_label="PARTIALLY_FILLED",
    )
    provider = ReconciliationFixtureProvider(BrokerOrderSearch((partial,), True))
    reconciled = await OrderReconciliationService(
        database, provider, lambda: now
    ).reconcile_unknown(order.id)

    assert reconciled.state == OrderState.PARTIALLY_FILLED
    assert reconciled.filled_quantity == Decimal("0.25")
    assert reconciled.to_dict()["remaining_quantity"] == "0.75"
    assert reconciled.provider_updated_at == partial.updated_at
    assert reconciled.provider_status_label == "PARTIALLY_FILLED"
    events = list_order_events(database, order_id=order.id, limit=100).items
    assert {event.event_type.value for event in events} >= {
        "reconciliation_result",
        "status_transition",
    }
    reconciliation_event = next(
        event for event in events if event.event_type.value == "reconciliation_result"
    )
    assert reconciliation_event.details["provider_status_label"] == "PARTIALLY_FILLED"

    later = now + timedelta(minutes=1)
    expired_repository = Database(f"sqlite:///{tmp_path / 'expired-portfolio.db'}")
    expired_order = await create_unknown_order(expired_repository, later)
    expired_snapshot = replace(
        reconciliation_snapshot(expired_order, state=OrderState.EXPIRED),
        status_label="EXPIRED",
        updated_at=later + timedelta(seconds=3),
    )
    expired_provider = ReconciliationFixtureProvider(
        BrokerOrderSearch((expired_snapshot,), True)
    )
    expired = await OrderReconciliationService(
        expired_repository, expired_provider, lambda: later
    ).reconcile_unknown(expired_order.id)

    assert expired.state == OrderState.EXPIRED
    assert expired.to_dict()["remaining_quantity"] is None


@pytest.mark.asyncio
async def test_stale_provider_observation_keeps_last_known_state(tmp_path) -> None:
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    order = await create_unknown_order(database, now)
    newer_at = now + timedelta(minutes=1)
    accepted = finish_order_reconciliation(
        database,
        order.id,
        attempt_id=uuid4(),
        expected_version=order.version,
        expected_state=OrderState.UNKNOWN,
        state=OrderState.ACCEPTED,
        now=newer_at,
        outcome="matched",
        result_code="matched",
        result_message="Broker order status was synchronized.",
        broker_order_id="broker-1",
        provider_updated_at=newer_at,
        provider_status_label="OPEN",
        result_source=OrderStatusSource.PROVIDER,
    )
    older = replace(
        reconciliation_snapshot(accepted),
        updated_at=now,
        state=OrderState.PARTIALLY_FILLED,
    )
    provider = ReconciliationFixtureProvider(older)
    result = await OrderReconciliationService(
        database, provider, lambda: newer_at + timedelta(seconds=30)
    ).sync(accepted.id)

    assert result.state == OrderState.ACCEPTED
    assert result.provider_updated_at == newer_at
    assert result.result_code == "stale"
    assert provider.reads == [("broker", accepted.account_id, "broker-1")]


@pytest.mark.asyncio
async def test_reconciliation_does_not_reduce_cumulative_fill(tmp_path) -> None:
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    order, _ = begin_order_submission(database, draft, now)
    order = mark_provider_submission_started(
        database, order.id, expected_version=order.version, started_at=now
    )
    order = finish_order_submission(
        database,
        order.id,
        OrderState.PARTIALLY_FILLED,
        now,
        expected_version=order.version,
        broker_order_id="broker-1",
        fill=FillSummary(Decimal("0.5"), Decimal("300")),
    )
    older_fill = replace(
        reconciliation_snapshot(order, state=OrderState.PARTIALLY_FILLED),
        fill=FillSummary(Decimal("0.25"), Decimal("299")),
    )
    provider = ReconciliationFixtureProvider(older_fill)

    result = await OrderReconciliationService(
        database, provider, lambda: now + timedelta(seconds=30)
    ).sync(order.id)

    assert result.state == OrderState.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("0.5")
    assert result.average_fill_price == Decimal("300")
    assert result.result_code == "refused"


@pytest.mark.asyncio
async def test_reconciliation_order_and_event_update_roll_back_together(
    tmp_path, monkeypatch
) -> None:
    import portfolio_mcp.reconciliation_store as reconciliation_store
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_history import OrderEventType
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    order = await create_unknown_order(database, now)
    snapshot = reconciliation_snapshot(order)
    provider = ReconciliationFixtureProvider(BrokerOrderSearch((snapshot,), True))
    append_event = reconciliation_store._append_order_event

    def fail_result_event(session, **kwargs) -> None:
        if kwargs["event_type"] == OrderEventType.RECONCILIATION_RESULT:
            raise RuntimeError("simulated event insert failure")
        append_event(session, **kwargs)

    monkeypatch.setattr(reconciliation_store, "_append_order_event", fail_result_event)
    with pytest.raises(RuntimeError, match="simulated event insert failure"):
        await OrderReconciliationService(
            database, provider, lambda: now
        ).reconcile_unknown(order.id)

    current = load_order(database, order.id)
    assert current is not None
    assert current.state == OrderState.UNKNOWN
    assert current.broker_order_id is None
    assert [
        event.event_type.value
        for event in list_order_events(database, order_id=order.id, limit=100).items
    ].count("reconciliation_attempted") == 1
    assert not any(
        event.event_type == OrderEventType.RECONCILIATION_RESULT
        for event in list_order_events(database, order_id=order.id, limit=100).items
    )


@pytest.mark.asyncio
async def test_competing_order_update_wins_over_reconciliation_result(tmp_path) -> None:
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_reconciliation import OrderReconciliationService
    from portfolio_mcp.stored_orders import ConcurrentOrderUpdate

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    order = await create_unknown_order(database, now)
    candidate = reconciliation_snapshot(order)

    class RacingProvider(ReconciliationFixtureProvider):
        async def find_by_client_order_id(
            self, account_id: str, client_order_id: str
        ) -> BrokerOrderSearch:
            self.reads.append(("client", account_id, client_order_id))
            finish_order_submission(
                database,
                order.id,
                OrderState.ACCEPTED,
                now + timedelta(seconds=1),
                expected_version=order.version,
                broker_order_id="raced-order",
            )
            return BrokerOrderSearch((candidate,), True)

    provider = RacingProvider(BrokerOrderSearch((candidate,), True))

    with pytest.raises(ConcurrentOrderUpdate):
        await OrderReconciliationService(
            database, provider, lambda: now
        ).reconcile_unknown(order.id)

    current = load_order(database, order.id)
    assert current is not None
    assert current.state == OrderState.ACCEPTED
    assert current.broker_order_id == "raced-order"
    assert provider.writes == []


@pytest.mark.asyncio
async def test_cancel_pending_sync_keeps_state_and_records_new_partial_fill(
    tmp_path,
) -> None:
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    order, created = begin_order_submission(database, draft, now)
    assert created
    order = mark_provider_submission_started(
        database, order.id, expected_version=order.version, started_at=now
    )
    order = finish_order_submission(
        database,
        order.id,
        OrderState.ACCEPTED,
        now,
        expected_version=order.version,
        broker_order_id="cancel-pending-order",
    )
    order = finish_order_submission(
        database,
        order.id,
        OrderState.CANCEL_PENDING,
        now + timedelta(seconds=1),
        expected_version=order.version,
    )
    observed = replace(
        reconciliation_snapshot(order, state=OrderState.PARTIALLY_FILLED),
        broker_order_id="cancel-pending-order",
        fill=FillSummary(Decimal("0.25"), None),
        status_label="PARTIALLY_FILLED",
    )
    provider = ReconciliationFixtureProvider(observed)

    synced = await OrderReconciliationService(
        database, provider, lambda: now + timedelta(seconds=30)
    ).sync(order.id)

    assert synced.state == OrderState.CANCEL_PENDING
    assert synced.filled_quantity == Decimal("0.25")
    assert synced.to_dict()["remaining_quantity"] == "0.75"
    result_event = next(
        event
        for event in list_order_events(database, order_id=order.id, limit=100).items
        if event.event_type.value == "reconciliation_result"
    )
    assert result_event.details["filled_quantity"] == "0.25"
    assert provider.writes == []


@pytest.mark.asyncio
async def test_sync_does_not_recover_a_live_submitting_order(tmp_path) -> None:
    from portfolio_mcp.execution import BrokerOrderSearch
    from portfolio_mcp.order_reconciliation import OrderReconciliationService

    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(database, now)
    order, created = begin_order_submission(database, draft, now)
    assert created
    provider = ReconciliationFixtureProvider(BrokerOrderSearch((), True))

    synced = await OrderReconciliationService(database, provider, lambda: now).sync(
        order.id
    )

    assert synced.state == OrderState.SUBMITTING
    assert synced.provider_submission_started_at is None
    assert provider.reads == []
