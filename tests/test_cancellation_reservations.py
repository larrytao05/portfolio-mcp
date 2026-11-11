from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from portfolio_mcp.database import PortfolioRepository


def test_reservation_is_atomic_and_survives_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'orders.sqlite'}"
    repository = PortfolioRepository(url)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(lambda _: repository.reserve_cancellation("a", "o", now), range(2))
        )
    assert sorted(created for _, created in outcomes) == [False, True]
    restored = PortfolioRepository(url)
    assert restored.cancellation_state("a", "o") == "UNKNOWN"
    assert restored.has_unresolved_cancellations("a")
    assert not restored.has_unresolved_cancellations("b")
    restored.finish_cancellation("a", "o", "CANCELED", now)
    assert not restored.has_unresolved_cancellations("a")
    assert restored.reserve_cancellation("a", "o", now) == ("CANCELED", False)


def test_cancellation_and_submission_cannot_both_reserve(tmp_path):
    from threading import Barrier

    from portfolio_mcp.database import OrderDraft

    url = f"sqlite:///{tmp_path / 'orders.sqlite'}"
    repository = PortfolioRepository(url)
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
    repository.save_order_draft(draft)
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
            repository.begin_order_submission(draft, now)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(cancel), pool.submit(submit)]
        results = [future.result() for future in futures]
    assert sorted(results) == [False, True]
