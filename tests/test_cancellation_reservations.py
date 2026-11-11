from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from portfolio_mcp.connection_store import ConnectionStore
from portfolio_mcp.database import Database
from portfolio_mcp.order_submission_store import (
    begin_order_submission,
    save_order_draft,
)


def test_reservation_is_atomic_and_survives_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'orders.sqlite'}"
    database = Database(url)
    repository = ConnectionStore(database)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repository.save_connection("c", "fixture", "fixture", "fixture", True)
    repository.save_account_route("a", "c", "upstream", trade_enabled=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(lambda _: repository.reserve_cancellation("a", "o", now), range(2))
        )
    assert sorted(created for _, created in outcomes) == [False, True]
    restored = ConnectionStore(Database(url))
    assert restored.cancellation_state("a", "o") == "UNKNOWN"
    assert restored.has_unresolved_cancellations("a")
    assert not restored.has_unresolved_cancellations("b")
    restored.finish_cancellation("a", "o", "CANCELED", now)
    assert not restored.has_unresolved_cancellations("a")
    assert restored.cancellation_attempts("a") == [
        {
            "broker_order_id": "o",
            "state": "CANCELED",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    ]
    assert restored.reserve_cancellation("a", "o", now) == ("CANCELED", False)


def test_cancellation_and_submission_cannot_both_reserve(tmp_path):
    from threading import Barrier

    from portfolio_mcp.stored_orders import OrderDraft

    url = f"sqlite:///{tmp_path / 'orders.sqlite'}"
    database = Database(url)
    repository = ConnectionStore(database)
    repository.save_connection("c", "alpaca", "live", "server_env:alpaca", True)
    repository.save_account_route("a", "c", "upstream", trade_enabled=True)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    from datetime import timedelta
    from decimal import Decimal

    draft = OrderDraft(
        "d",
        "a",
        "A",
        "Alpaca",
        "us-equity:VTI",
        "VTI",
        "VTI",
        "equity",
        "buy",
        "limit",
        Decimal("1"),
        Decimal("100"),
        now,
        None,
        None,
        None,
        "test",
        Decimal("100"),
        now,
        now,
        now,
        (),
        "fingerprint",
        now,
        now + timedelta(minutes=5),
    )
    save_order_draft(database, draft)
    barrier = Barrier(2)

    def cancel():
        barrier.wait()
        try:
            repository.reserve_cancellation("a", "broker-order", now)
            return True
        except ValueError:
            return False

    def submit():
        barrier.wait()
        try:
            begin_order_submission(database, draft, now)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(cancel), pool.submit(submit)]
        results = [future.result() for future in futures]
    assert sorted(results) == [False, True]


def test_event_insert_failure_rolls_back_reservation_and_result(tmp_path):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    database = Database(f"sqlite:///{tmp_path / 'atomic.sqlite'}")
    routes = ConnectionStore(database)
    routes.save_connection("fixture", "fixture", "fixture", "fixture", True)
    routes.save_account_route("a", "fixture", "a", trade_enabled=True)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_event BEFORE INSERT ON "
                "imported_cancellation_events "
                "BEGIN SELECT RAISE(ABORT, 'rejected'); END"
            )
        )
    with pytest.raises(SQLAlchemyError):
        routes.reserve_cancellation("a", "order", now)
    assert routes.cancellation_state("a", "order") is None
    with database.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER reject_event"))
    routes.reserve_cancellation("a", "order", now)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_event BEFORE INSERT ON "
                "imported_cancellation_events "
                "BEGIN SELECT RAISE(ABORT, 'rejected'); END"
            )
        )
    with pytest.raises(SQLAlchemyError):
        routes.finish_cancellation("a", "order", "CANCELED", now)
    assert routes.cancellation_state("a", "order") == "UNKNOWN"
    assert [
        (event["kind"], event["state"]) for event in routes.cancellation_events("a")
    ] == [("reserved", "UNKNOWN")]


def test_imported_cancellation_events_are_append_only(tmp_path):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    database = Database(f"sqlite:///{tmp_path / 'audit.sqlite'}")
    routes = ConnectionStore(database)
    routes.save_connection("fixture", "fixture", "fixture", "fixture", True)
    routes.save_account_route("a", "fixture", "a")
    routes.reserve_cancellation("a", "o", datetime(2026, 1, 1, tzinfo=UTC))
    for statement in (
        "UPDATE imported_cancellation_events SET state = 'CANCELED'",
        "DELETE FROM imported_cancellation_events",
        "DELETE FROM cancellation_attempts",
        "UPDATE cancellation_attempts SET broker_order_id = 'changed'",
    ):
        with pytest.raises(SQLAlchemyError), database.engine.begin() as connection:
            connection.execute(text(statement))
    assert routes.cancellation_state("a", "o") == "UNKNOWN"
    assert len(routes.cancellation_events("a")) == 1


def test_dispatch_refuses_concurrent_disconnect_without_holding_database_write_lock(
    tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    import pytest

    from portfolio_mcp.connection_store import ConnectionBusyError
    from portfolio_mcp.trading_safety import TradingSettingsService

    database = Database(f"sqlite:///{tmp_path / 'dispatch.sqlite'}")
    routes = ConnectionStore(database)
    routes.save_connection("live", "alpaca", "live", "server_env:alpaca", True)
    routes.save_account_route("account", "live", "upstream", trade_enabled=True)
    route = routes.account_route("account")
    assert route is not None
    entered, release = Event(), Event()

    def dispatch():
        entered.set()
        assert release.wait(5)
        return 204, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(routes.dispatch_cancellation, route, dispatch)
        assert entered.wait(5)
        with pytest.raises(ConnectionBusyError):
            routes.disconnect_connection("live")
        settings = TradingSettingsService(database, lambda: datetime.now(UTC))
        settings.replace(
            live_trading_enabled=False,
            kill_switch_active=True,
            max_order_shares="1",
            max_order_notional_usd="100",
            version=0,
        )
        release.set()
        assert first.result() == (204, None)
    assert routes.disconnect_connection("live")
    final_route = routes.account_route("account")
    assert final_route is not None and not final_route.active


def _disconnect_in_process(database_url, result_queue):
    from portfolio_mcp.connection_store import ConnectionBusyError, ConnectionStore
    from portfolio_mcp.database import Database

    try:
        ConnectionStore(Database(database_url)).disconnect_connection("live")
    except ConnectionBusyError:
        result_queue.put("busy")
    else:
        result_queue.put("disconnected")


def test_connection_dispatch_lock_is_shared_between_processes(tmp_path):
    import multiprocessing
    from threading import Event, Thread

    database_url = f"sqlite:///{tmp_path / 'cross-process.sqlite'}"
    routes = ConnectionStore(Database(database_url))
    routes.save_connection("live", "alpaca", "live", "server_env:alpaca", True)
    routes.save_account_route("account", "live", "upstream", trade_enabled=True)
    route = routes.account_route("account")
    assert route is not None
    entered, release = Event(), Event()

    def dispatch():
        entered.set()
        assert release.wait(15)
        return 204, None

    thread = Thread(target=routes.dispatch_cancellation, args=(route, dispatch))
    thread.start()
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(
        target=_disconnect_in_process, args=(database_url, results)
    )
    try:
        assert entered.wait(5)
        process.start()
        assert results.get(timeout=10) == "busy"
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        release.set()
        thread.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
    final_route = routes.account_route("account")
    assert final_route is not None and final_route.active
