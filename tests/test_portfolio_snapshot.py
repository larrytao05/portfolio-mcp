from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from portfolio_mcp.database import Database
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.overview import OverviewService
from portfolio_mcp.portfolio_store import latest_refresh as load_latest_refresh
from portfolio_mcp.portfolio_store import (
    provider_health,
    save_failed_refresh,
    save_refresh,
)
from portfolio_mcp.refresh import PortfolioRefreshService


@pytest.mark.asyncio
async def test_overview_keeps_one_read_snapshot_during_a_committed_refresh(tmp_path):
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    url = f"sqlite:///{tmp_path / 'snapshot.db'}"
    reader = Database(url, clock=lambda: now)
    writer = Database(url, clock=lambda: now + timedelta(minutes=1))
    engine = reader.engine
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    await PortfolioRefreshService(
        FixturePortfolioProvider(), reader, lambda: now
    ).refresh()
    baseline = OverviewService(reader).get_overview()
    committed = False

    def commit_after_first_read(
        connection, cursor, statement, parameters, context, many
    ):
        nonlocal committed
        if committed or not statement.lstrip().upper().startswith("SELECT"):
            return
        committed = True
        later = now + timedelta(minutes=1)
        save_failed_refresh(
            writer, later, later, "authentication_required", "Fixture failure"
        )

    event.listen(engine, "after_cursor_execute", commit_after_first_read)
    try:
        overview = OverviewService(reader).get_overview()
    finally:
        event.remove(engine, "after_cursor_execute", commit_after_first_read)
    assert committed
    assert overview == baseline
    assert overview.status == "fresh"
    assert overview.refreshed_at == now
    assert str(overview.total_known_usd_value) == "9999.95"
    assert not any(account.is_stale for account in overview.accounts)
    assert all(coverage.status == "fresh" for coverage in overview.provider_coverage)
    latest = load_latest_refresh(writer)
    assert latest is not None
    assert latest.status == "failed"
    assert latest.completed_at == now + timedelta(minutes=1)
    updated = OverviewService(reader).get_overview()
    assert updated.status == "stale"
    assert updated.refreshed_at == latest.completed_at
    assert updated.total_known_usd_value is None
    assert all(account.is_stale for account in updated.accounts)
    assert all(coverage.status == "stale" for coverage in updated.provider_coverage)
    assert updated.history == baseline.history


@pytest.mark.asyncio
async def test_removed_account_coverage_at_same_provider_survives_repeat_and_return(
    tmp_path,
):
    from dataclasses import replace

    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'coverage.db'}", lambda: now)
    provider = FixturePortfolioProvider()
    snapshots = [
        await provider.get_holdings(account.id)
        for account in await provider.list_accounts()
    ]
    snapshots = [
        replace(snapshot, account=replace(snapshot.account, provider="Schwab"))
        for snapshot in snapshots
    ]
    save_refresh(database, snapshots, now, now, now.date())
    for _ in range(2):
        result = save_refresh(database, snapshots[:1], now, now, now.date())
        assert result.status == "partial"
        assert len(result.provider_outcomes) == 1
        outcome = result.provider_outcomes[0]
        assert outcome.provider == "Schwab"
        assert outcome.accounts_refreshed == 1
        assert outcome.stale_accounts == 1
        assert outcome.excluded_accounts == 0
        assert outcome.warning is not None
        overview = OverviewService(database).get_overview()
        assert str(overview.total_known_usd_value) == "4799.97"
        assert overview.provider_coverage[0].status == "partial"
        assert overview.provider_coverage[0].stale_accounts == 1
        assert provider_health(database)[0].state == "degraded"
    result = save_refresh(database, snapshots, now, now, now.date())
    assert result.status == "success"
    assert result.provider_outcomes[0].stale_accounts == 0
    overview = OverviewService(database).get_overview()
    assert str(overview.total_known_usd_value) == "9999.95"
    assert all(not account.is_stale for account in overview.accounts)
    assert overview.provider_coverage[0].status == "fresh"


@pytest.mark.asyncio
async def test_provider_health_keeps_one_read_snapshot_during_committed_failure(
    tmp_path,
):
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    later = now + timedelta(minutes=1)
    url = f"sqlite:///{tmp_path / 'health-snapshot.db'}"
    reader = Database(url, lambda: now)
    writer = Database(url, lambda: later)
    with reader.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    await PortfolioRefreshService(
        FixturePortfolioProvider(), reader, lambda: now
    ).refresh()
    baseline = provider_health(reader)
    committed = False

    def commit_after_first_read(
        connection, cursor, statement, parameters, context, many
    ):
        nonlocal committed
        if committed or not statement.lstrip().upper().startswith("SELECT"):
            return
        committed = True
        save_failed_refresh(
            writer, later, later, "authentication_required", "Fixture failure"
        )

    event.listen(reader.engine, "after_cursor_execute", commit_after_first_read)
    try:
        health = provider_health(reader)
    finally:
        event.remove(reader.engine, "after_cursor_execute", commit_after_first_read)
    assert committed
    assert health == baseline
    assert all(item.state == "healthy" and item.observed_at == now for item in health)
    updated = provider_health(reader)
    assert all(item.state == "authentication_required" for item in updated)
    assert all(item.observed_at == later for item in updated)
    assert all(item.last_success_at == now for item in updated)


@pytest.mark.asyncio
async def test_refresh_coverage_deduplicates_failures_and_retains_removed_provider(
    tmp_path,
):
    from dataclasses import replace

    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    database = Database(
        f"sqlite:///{tmp_path / 'deduplicated-coverage.db'}", lambda: now
    )
    provider = FixturePortfolioProvider()
    snapshots = [
        await provider.get_holdings(account.id)
        for account in await provider.list_accounts()
    ]
    first, removed = snapshots
    save_refresh(database, snapshots, now, now, now.date())
    missing = replace(first.account, id="never-stored")
    for _ in range(2):
        result = save_refresh(
            database,
            [first, first],
            now,
            now,
            now.date(),
            failed_accounts=[missing, missing, first.account],
        )
        assert result.status == "partial"
        assert result.account_count == 1
        outcomes = {item.provider: item for item in result.provider_outcomes}
        assert outcomes[first.account.provider].accounts_refreshed == 1
        assert outcomes[first.account.provider].excluded_accounts == 1
        assert outcomes[first.account.provider].stale_accounts == 0
        assert outcomes[removed.account.provider].accounts_refreshed == 0
        assert outcomes[removed.account.provider].stale_accounts == 1
        assert outcomes[removed.account.provider].excluded_accounts == 0
        assert outcomes[removed.account.provider].status == "failed"
        assert load_latest_refresh(database) == result
    failed = save_refresh(
        database,
        [],
        now,
        now,
        now.date(),
        failed_accounts=[first.account, first.account, missing, missing],
    )
    assert failed.status == "failed"
    assert sum(item.stale_accounts for item in failed.provider_outcomes) == 2
    assert sum(item.excluded_accounts for item in failed.provider_outcomes) == 1
    restored = save_refresh(database, snapshots, now, now, now.date())
    assert restored.status == "success"
    assert all(
        item.stale_accounts == 0 and item.excluded_accounts == 0
        for item in restored.provider_outcomes
    )
