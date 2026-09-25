import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import IntegrityError as SQLiteIntegrityError
from sqlite3 import connect
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from alembic import command
from portfolio_mcp.database import ConcurrentOrderUpdate, PortfolioRepository
from portfolio_mcp.execution import FixtureExecutionProvider, OrderState
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventCode,
    OrderEventType,
    OrderStatusSource,
    encode_event_details,
)
from portfolio_mcp.trading_service import (
    OrderSubmissionService,
    TradingValidationError,
    allow_fixture_submission,
)
from tests.test_execution import create_limit_draft, enabled_order_draft_service


@pytest.mark.asyncio
async def test_draft_created_event_survives_repository_reopen(tmp_path) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    repository = PortfolioRepository(database_url, lambda: now)
    draft = await create_limit_draft(repository, now)

    first_page = repository.list_order_events(draft_id=draft.id, limit=50)

    assert len(first_page.items) == 1
    event = first_page.items[0]
    assert event.event_type == "draft_created"
    assert event.draft_id == draft.id
    assert event.order_id is None
    assert event.actor == "dashboard"
    assert event.previous_state is None
    assert event.next_state is None
    assert event.occurred_at == now
    assert first_page.next_cursor is None

    reopened = PortfolioRepository(database_url, lambda: now)
    persisted = reopened.list_order_events(draft_id=draft.id, limit=50)

    assert persisted.items == first_page.items


def test_order_detail_has_a_compact_legacy_safe_draft_summary(tmp_path) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = asyncio.run(create_limit_draft(repository, now))
    order, created = repository.begin_order_submission(draft, now)

    assert created
    summary = order.to_dict()["draft"]
    assert summary == {
        "id": draft.id,
        "created_at": draft.created_at.isoformat(),
        "expires_at": draft.expires_at.isoformat(),
        "instruction": {
            "instrument_id": draft.instrument_id,
            "symbol": draft.symbol,
            "side": draft.side,
            "type": draft.order_type,
            "quantity": str(draft.quantity),
            "limit_price": str(draft.limit_price),
        },
    }
    assert order.state == OrderState.SUBMITTING


@pytest.mark.asyncio
async def test_submission_persists_authorization_and_fill_events(tmp_path) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    repository = PortfolioRepository(database_url, lambda: now)
    draft = await create_limit_draft(repository, now)
    service = OrderSubmissionService(
        repository,
        FixtureExecutionProvider("partial_fill"),
        lambda: now,
        allow_fixture_submission,
    )

    order = await service.confirm(draft.id, draft.fingerprint, True)
    events = repository.list_order_events(draft_id=draft.id, limit=20).items

    assert order.state == OrderState.PARTIALLY_FILLED
    assert order.filled_quantity == order.quantity / 2
    assert order.average_fill_price is not None
    assert order.result_source == OrderStatusSource.PROVIDER
    assert {event.event_type for event in events} == {
        OrderEventType.DRAFT_CREATED,
        OrderEventType.AUTHORIZATION_CREATED,
        OrderEventType.AUTHORIZATION_CONSUMED,
        OrderEventType.SUBMISSION_STARTED,
        OrderEventType.SUBMISSION_RESULT,
    }
    result_event = next(
        event
        for event in events
        if event.event_type == OrderEventType.SUBMISSION_RESULT
    )
    assert result_event.actor == OrderEventActor.DASHBOARD
    assert result_event.next_state == OrderState.PARTIALLY_FILLED.value
    assert result_event.code == OrderEventCode.PARTIALLY_FILLED
    assert result_event.details["filled_quantity"] == str(order.filled_quantity)
    assert "message" not in result_event.details
    serialized_fill = order.to_dict()["fill"]
    assert isinstance(serialized_fill, dict)
    assert serialized_fill["quantity"] == str(order.filled_quantity)


@pytest.mark.asyncio
async def test_expired_confirmation_records_one_expiry_and_auth_failure(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(repository, now)
    expired_at = draft.expires_at + timedelta(seconds=1)
    service = OrderSubmissionService(
        repository,
        FixtureExecutionProvider(),
        lambda: expired_at,
        allow_fixture_submission,
    )

    for _ in range(2):
        with pytest.raises(TradingValidationError, match="expired"):
            await service.confirm(draft.id, draft.fingerprint, True)

    events = repository.list_order_events(draft_id=draft.id, limit=20).items
    assert (
        sum(event.event_type == OrderEventType.DRAFT_EXPIRED for event in events) == 1
    )
    assert (
        sum(event.event_type == OrderEventType.AUTHORIZATION_FAILED for event in events)
        == 2
    )
    assert all(event.actor == OrderEventActor.DASHBOARD for event in events)


@pytest.mark.asyncio
async def test_unknown_draft_failure_does_not_persist_the_supplied_identifier(
    tmp_path,
) -> None:
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    service = OrderSubmissionService(
        repository,
        FixtureExecutionProvider(),
        lambda: datetime(2026, 9, 25, 14, 0, tzinfo=UTC),
        allow_fixture_submission,
    )

    with pytest.raises(TradingValidationError, match="not found"):
        await service.confirm("attacker-controlled-id", "invalid", True)

    event = repository.list_order_events(limit=20).items[0]
    assert event.event_type == OrderEventType.AUTHORIZATION_FAILED
    assert event.draft_id is None
    assert event.account_id is None
    assert "attacker-controlled-id" not in str(event.to_dict())


def test_event_details_reject_fields_outside_the_event_allowlist() -> None:
    with pytest.raises(ValueError, match="Invalid details"):
        encode_event_details(
            OrderEventType.AUTHORIZATION_FAILED,
            {
                "attempt_id": uuid4(),
                "action": "submit",
                "authorization_code": "secret",
            },
        )


@pytest.mark.asyncio
async def test_event_and_order_pages_are_tie_safe_and_filter_bound(tmp_path) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    orders = []
    draft_service = await enabled_order_draft_service(
        repository,
        FixturePortfolioProvider(),
        FixtureMarketDataProvider(),
        lambda: now,
    )
    for _ in range(3):
        draft = await draft_service.create(
            account_id="schwab-taxable-demo",
            instrument_id="us-etf:VTI",
            side="buy",
            order_type="limit",
            quantity="1",
            limit_price="300.25",
        )
        orders.append(repository.begin_order_submission(draft, now)[0])

    event_ids = []
    cursor = None
    while True:
        page = repository.list_order_events(limit=1, after=cursor)
        event_ids.extend(event.event_id for event in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    order_ids = []
    order_cursor = None
    while True:
        page = repository.list_orders(limit=1, after=order_cursor)
        order_ids.extend(order.id for order in page.items)
        order_cursor = page.next_cursor
        if order_cursor is None:
            break

    assert len(event_ids) == 12
    assert len(set(event_ids)) == 12
    assert set(order_ids) == {order.id for order in orders}
    assert len(order_ids) == len(set(order_ids)) == 3
    first_page_cursor = repository.list_orders(limit=1).next_cursor
    assert first_page_cursor is not None
    with pytest.raises(ValueError, match="cursor does not match"):
        repository.list_orders(limit=1, after=first_page_cursor, account_id="another")


@pytest.mark.asyncio
async def test_event_insert_failure_rolls_back_order_state_update(tmp_path) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    database_path = tmp_path / "portfolio.db"
    database_url = f"sqlite:///{database_path}"
    repository = PortfolioRepository(database_url, lambda: now)
    draft = await create_limit_draft(repository, now)
    order, _ = repository.begin_order_submission(draft, now)
    with connect(database_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_submission_result BEFORE INSERT ON order_events "
            "WHEN NEW.event_type = 'submission_result' BEGIN "
            "SELECT RAISE(ABORT, 'test event failure'); END"
        )

    with pytest.raises(IntegrityError, match="test event failure"):
        repository.finish_order_submission(
            order.id,
            OrderState.ACCEPTED,
            now,
            expected_version=order.version,
            result_code="accepted",
            result_message="Order was accepted.",
            result_source=OrderStatusSource.PROVIDER,
            actor=OrderEventActor.DASHBOARD,
        )

    stored = repository.order(order.id)
    assert stored is not None
    assert stored.state == OrderState.SUBMITTING
    assert not any(
        event.event_type == OrderEventType.SUBMISSION_RESULT
        for event in repository.list_order_events(draft_id=draft.id).items
    )


@pytest.mark.asyncio
async def test_event_rows_reject_updates_and_deletes(tmp_path) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    database_path = tmp_path / "portfolio.db"
    repository = PortfolioRepository(f"sqlite:///{database_path}")
    draft = await create_limit_draft(repository, now)
    event_id = repository.list_order_events(draft_id=draft.id).items[0].event_id

    with connect(database_path) as connection:
        with pytest.raises(SQLiteIntegrityError, match="append-only"):
            connection.execute(
                "UPDATE order_events SET details_json = '{}' WHERE event_id = ?",
                (str(event_id),),
            )
        with pytest.raises(SQLiteIntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM order_events WHERE event_id = ?", (str(event_id),)
            )


@pytest.mark.asyncio
async def test_event_reference_triggers_reject_unknown_drafts(tmp_path) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    database_path = tmp_path / "portfolio.db"
    repository = PortfolioRepository(f"sqlite:///{database_path}")
    draft = await create_limit_draft(repository, now)

    with connect(database_path) as connection:
        with pytest.raises(SQLiteIntegrityError, match="invalid order event draft"):
            connection.execute(
                "INSERT INTO order_events "
                "(event_id, draft_id, order_id, account_id, event_type, actor, "
                "previous_state, next_state, code, details_schema_version, "
                "details_json, deduplication_key, occurred_at) "
                "VALUES (?, ?, NULL, ?, 'draft_created', 'dashboard', NULL, "
                "NULL, NULL, 1, '{}', ?, ?)",
                (
                    str(uuid4()),
                    "unrecognized-draft",
                    draft.account_id,
                    str(uuid4()),
                    now.isoformat(),
                ),
            )


@pytest.mark.asyncio
async def test_recovery_event_is_atomic_and_recovery_is_idempotent(tmp_path) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(repository, now)
    order, _ = repository.begin_order_submission(draft, now)

    assert repository.recover_stranded_submissions(now) == 1
    assert repository.recover_stranded_submissions(now) == 0
    events = repository.list_order_events(order_id=order.id, limit=50).items

    assert (
        sum(event.event_type == OrderEventType.STATUS_TRANSITION for event in events)
        == 1
    )
    recovery = next(
        event
        for event in events
        if event.event_type == OrderEventType.STATUS_TRANSITION
    )
    assert recovery.actor == OrderEventActor.SYSTEM
    assert recovery.previous_state == OrderState.SUBMITTING.value
    assert recovery.next_state == OrderState.UNKNOWN.value


@pytest.mark.asyncio
async def test_stale_submission_result_cannot_overwrite_recovered_unknown(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    draft = await create_limit_draft(repository, now)
    order, _ = repository.begin_order_submission(draft, now)
    repository.recover_stranded_submissions(now)

    with pytest.raises(ConcurrentOrderUpdate):
        repository.finish_order_submission(
            order.id,
            OrderState.ACCEPTED,
            now,
            expected_version=order.version,
            result_code="accepted",
            result_source=OrderStatusSource.PROVIDER,
        )

    stored = repository.order(order.id)
    assert stored is not None
    assert stored.state == OrderState.UNKNOWN


@pytest.mark.parametrize("base_revision", ["20260922_0009", "20260925_0010"])
def test_pre_migration_order_remains_readable_without_synthetic_history(
    tmp_path, base_revision: str
) -> None:
    database_path = tmp_path / f"legacy-{base_revision}.db"
    database_url = f"sqlite:///{database_path}"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, base_revision)
    now = datetime(2026, 9, 25, 14, 0, tzinfo=UTC).isoformat()
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO order_drafts "
            "(id, account_id, account_label, provider, instrument_id, symbol, "
            "instrument_name, asset_class, side, order_type, quantity, "
            "limit_price, warnings, fingerprint, created_at, expires_at, "
            "estimated_notional, account_refreshed_at, capability_observed_at, "
            "capability_last_success_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "draft-legacy",
                "schwab-taxable-demo",
                "Schwab Taxable",
                "Schwab",
                "us-etf:VTI",
                "VTI",
                "Vanguard Total Stock Market ETF",
                "equity_etf",
                "buy",
                "limit",
                "1",
                "300.25",
                "[]",
                "f" * 64,
                now,
                now,
                None,
                None,
                None,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO orders "
            "(id, draft_id, client_order_id, fingerprint, account_id, account_label, "
            "provider, instrument_id, symbol, side, order_type, quantity, limit_price, "
            "state, broker_order_id, result_code, result_message, created_at, "
            "updated_at, version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "order-legacy",
                "draft-legacy",
                "client-legacy",
                "f" * 64,
                "schwab-taxable-demo",
                "Schwab Taxable",
                "Schwab",
                "us-etf:VTI",
                "VTI",
                "buy",
                "limit",
                "1",
                "300.25",
                "ACCEPTED",
                None,
                "accepted",
                "Order was accepted.",
                now,
                now,
                2,
            ),
        )

    repository = PortfolioRepository(database_url)
    order = repository.order("order-legacy")

    assert order is not None
    assert order.state == OrderState.ACCEPTED
    assert order.filled_quantity is None
    assert order.average_fill_price is None
    assert order.result_source is None
    assert order.draft.id == "draft-legacy"
    assert repository.list_order_events(order_id=order.id).items == ()
