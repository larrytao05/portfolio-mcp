from datetime import UTC, date, datetime
from decimal import Decimal

from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.overview import (
    AllocationGroup,
    GainLossCoverage,
    OverviewService,
    PortfolioOverview,
    RecordedHistory,
)


def test_empty_portfolio(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'empty.db'}"
    repository = PortfolioRepository(database_url)
    service = OverviewService(repository)

    overview = service.get_overview()

    assert isinstance(overview, PortfolioOverview)
    assert overview.status == "empty"
    assert overview.total_known_usd_value is None
    assert overview.cash_usd is None
    assert overview.buying_power_usd is None
    assert overview.as_of is None
    assert overview.refreshed_at is None
    assert overview.accounts == ()
    assert overview.exclusions == ()
    assert overview.warnings == ()

    assert "account" in overview.allocations
    assert "asset_class" in overview.allocations
    account_alloc = overview.allocations["account"]
    assert isinstance(account_alloc, AllocationGroup)
    assert account_alloc.group_by == "account"
    assert account_alloc.denominator == Decimal("0")
    assert account_alloc.slices == ()
    assert account_alloc.included_count == 0
    assert account_alloc.excluded_count == 0

    asset_alloc = overview.allocations["asset_class"]
    assert isinstance(asset_alloc, AllocationGroup)
    assert asset_alloc.group_by == "asset_class"
    assert asset_alloc.denominator == Decimal("0")
    assert asset_alloc.slices == ()
    assert asset_alloc.included_count == 0
    assert asset_alloc.excluded_count == 0

    assert isinstance(overview.gain_loss, GainLossCoverage)
    assert overview.gain_loss.unrealized_gain_loss is None
    assert overview.gain_loss.cost_basis is None
    assert overview.gain_loss.market_value is None
    assert overview.gain_loss.included_count == 0
    assert overview.gain_loss.excluded_count == 0

    assert isinstance(overview.history, RecordedHistory)
    assert overview.history.points == ()
    assert overview.history.currencies == ()


def test_multi_account_usd_portfolio(tmp_path) -> None:
    from portfolio_mcp.models import Account, HoldingsSnapshot, Position

    database_url = f"sqlite:///{tmp_path / 'multi.db'}"
    repository = PortfolioRepository(database_url)

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    snap_date = date(2026, 8, 29)

    acc1 = Account(
        id="acc-1",
        provider="Schwab",
        label="Schwab Taxable",
        account_type="taxable_brokerage",
        currency="USD",
    )
    pos1 = [
        Position(
            account_id="acc-1",
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class="equity_etf",
            quantity=Decimal("9"),
            current_price=Decimal("333.3333"),
            market_value=Decimal("3000.00"),
            cost_basis=Decimal("2500.00"),
            currency="USD",
        ),
        Position(
            account_id="acc-1",
            symbol="GLD",
            name="SPDR Gold Shares",
            asset_class="commodity_etf",
            quantity=Decimal("2"),
            current_price=Decimal("500.00"),
            market_value=Decimal("1000.00"),
            cost_basis=Decimal("900.00"),
            currency="USD",
        ),
    ]

    acc2 = Account(
        id="acc-2",
        provider="Fidelity",
        label="Fidelity Roth IRA",
        account_type="roth_ira",
        currency="USD",
    )
    pos2 = [
        Position(
            account_id="acc-2",
            symbol="BND",
            name="Vanguard Total Bond Market ETF",
            asset_class="bond_etf",
            quantity=Decimal("50"),
            current_price=Decimal("80.00"),
            market_value=Decimal("4000.00"),
            cost_basis=Decimal("3800.00"),
            currency="USD",
        ),
        Position(
            account_id="acc-2",
            symbol="AAPL",
            name="Apple Inc.",
            asset_class="equity",
            quantity=Decimal("10"),
            current_price=Decimal("200.00"),
            market_value=Decimal("2000.00"),
            cost_basis=Decimal("1800.00"),
            currency="USD",
        ),
    ]

    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc1, as_of=snap_date, positions=tuple(pos1)),
            HoldingsSnapshot(account=acc2, as_of=snap_date, positions=tuple(pos2)),
        ],
        started_at=now,
        completed_at=now,
        snapshot_date=snap_date,
    )

    service = OverviewService(repository)
    overview = service.get_overview()

    assert overview.status == "fresh"
    assert overview.total_known_usd_value == Decimal("10000.00")
    assert overview.as_of == snap_date
    assert overview.refreshed_at == now
    assert len(overview.accounts) == 2

    # Check accounts
    acc_map = {a.account_id: a for a in overview.accounts}
    assert acc_map["acc-1"].market_value == Decimal("4000.00")
    assert acc_map["acc-1"].percentage_of_total == Decimal("0.4000")
    assert acc_map["acc-1"].is_stale is False

    assert acc_map["acc-2"].market_value == Decimal("6000.00")
    assert acc_map["acc-2"].percentage_of_total == Decimal("0.6000")
    assert acc_map["acc-2"].is_stale is False

    pct1 = acc_map["acc-1"].percentage_of_total
    pct2 = acc_map["acc-2"].percentage_of_total
    assert pct1 is not None and pct2 is not None
    assert pct1 + pct2 == Decimal("1.0000")

    # Allocations by account
    account_alloc = overview.allocations["account"]
    assert account_alloc.denominator == Decimal("10000.00")
    assert account_alloc.included_count == 4
    assert account_alloc.excluded_count == 0
    alloc_acc_map = {s.key: s for s in account_alloc.slices}
    assert alloc_acc_map["acc-1"].amount == Decimal("4000.00")
    assert alloc_acc_map["acc-1"].percentage == Decimal("0.4000")
    assert alloc_acc_map["acc-1"].position_count == 2
    assert alloc_acc_map["acc-2"].amount == Decimal("6000.00")
    assert alloc_acc_map["acc-2"].percentage == Decimal("0.6000")
    assert alloc_acc_map["acc-2"].position_count == 2

    # Allocations by asset class
    asset_alloc = overview.allocations["asset_class"]
    assert asset_alloc.denominator == Decimal("10000.00")
    assert asset_alloc.included_count == 4
    assert asset_alloc.excluded_count == 0
    assert sum(s.amount for s in asset_alloc.slices) == Decimal("10000.00")
    assert sum(s.percentage for s in asset_alloc.slices) == Decimal("1.0000")
    asset_map = {s.key: s for s in asset_alloc.slices}
    assert asset_map["bond_etf"].amount == Decimal("4000.00")
    assert asset_map["bond_etf"].percentage == Decimal("0.4000")
    assert asset_map["equity_etf"].amount == Decimal("3000.00")
    assert asset_map["equity_etf"].percentage == Decimal("0.3000")
    assert asset_map["equity"].amount == Decimal("2000.00")
    assert asset_map["equity"].percentage == Decimal("0.2000")
    assert asset_map["commodity_etf"].amount == Decimal("1000.00")
    assert asset_map["commodity_etf"].percentage == Decimal("0.1000")

    # Gain loss coverage
    assert overview.gain_loss.unrealized_gain_loss == Decimal("1000.00")
    assert overview.gain_loss.cost_basis == Decimal("9000.00")
    assert overview.gain_loss.market_value == Decimal("10000.00")
    assert overview.gain_loss.included_count == 4
    assert overview.gain_loss.excluded_count == 0


def test_duplicate_symbols_across_accounts_preserve_identities(tmp_path) -> None:
    from portfolio_mcp.models import Account, HoldingsSnapshot, Position

    database_url = f"sqlite:///{tmp_path / 'dup.db'}"
    repository = PortfolioRepository(database_url)

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    snap_date = date(2026, 8, 29)

    acc1 = Account(
        id="acc-schwab",
        provider="Schwab",
        label="Schwab Brokerage",
        account_type="taxable_brokerage",
        currency="USD",
    )
    pos1 = [
        Position(
            account_id="acc-schwab",
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class="equity_etf",
            quantity=Decimal("9"),
            current_price=Decimal("333.3333"),
            market_value=Decimal("3000.00"),
            cost_basis=Decimal("2700.00"),
            currency="USD",
        ),
    ]

    acc2 = Account(
        id="acc-fidelity",
        provider="Fidelity",
        label="Fidelity IRA",
        account_type="roth_ira",
        currency="USD",
    )
    pos2 = [
        Position(
            account_id="acc-fidelity",
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class="equity_etf",
            quantity=Decimal("6"),
            current_price=Decimal("333.3333"),
            market_value=Decimal("2000.00"),
            cost_basis=Decimal("1800.00"),
            currency="USD",
        ),
    ]

    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc1, as_of=snap_date, positions=tuple(pos1)),
            HoldingsSnapshot(account=acc2, as_of=snap_date, positions=tuple(pos2)),
        ],
        started_at=now,
        completed_at=now,
        snapshot_date=snap_date,
    )

    service = OverviewService(repository)
    overview = service.get_overview()

    assert overview.total_known_usd_value == Decimal("5000.00")

    acc_map = {a.account_id: a for a in overview.accounts}
    assert acc_map["acc-schwab"].market_value == Decimal("3000.00")
    assert acc_map["acc-schwab"].percentage_of_total == Decimal("0.6000")
    assert acc_map["acc-fidelity"].market_value == Decimal("2000.00")
    assert acc_map["acc-fidelity"].percentage_of_total == Decimal("0.4000")

    asset_alloc = overview.allocations["asset_class"]
    assert len(asset_alloc.slices) == 1
    vti_slice = asset_alloc.slices[0]
    assert vti_slice.key == "equity_etf"
    assert vti_slice.amount == Decimal("5000.00")
    assert vti_slice.percentage == Decimal("1.0000")
    assert vti_slice.position_count == 2

    assert overview.gain_loss.cost_basis == Decimal("4500.00")
    assert overview.gain_loss.market_value == Decimal("5000.00")
    assert overview.gain_loss.unrealized_gain_loss == Decimal("500.00")
    assert overview.gain_loss.included_count == 2
    assert overview.gain_loss.excluded_count == 0


def test_missing_market_value_and_cost_basis(tmp_path) -> None:
    from portfolio_mcp.models import Account, HoldingsSnapshot, Position

    database_url = f"sqlite:///{tmp_path / 'missing.db'}"
    repository = PortfolioRepository(database_url)

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    snap_date = date(2026, 8, 29)

    acc = Account(
        id="acc-test",
        provider="Schwab",
        label="Schwab Test",
        account_type="taxable_brokerage",
        currency="USD",
    )
    positions = [
        Position(
            account_id="acc-test",
            symbol="AAPL",
            name="Apple Inc.",
            asset_class="equity",
            quantity=Decimal("10"),
            current_price=Decimal("100.00"),
            market_value=Decimal("1000.00"),
            cost_basis=Decimal("800.00"),
            currency="USD",
        ),
        Position(
            account_id="acc-test",
            symbol="PRIV",
            name="Private Equity Holding",
            asset_class="equity",
            quantity=Decimal("5"),
            current_price=None,
            market_value=None,
            cost_basis=Decimal("500.00"),
            currency="USD",
        ),
        Position(
            account_id="acc-test",
            symbol="GIFT",
            name="Gifted Holding",
            asset_class="equity",
            quantity=Decimal("5"),
            current_price=Decimal("100.00"),
            market_value=Decimal("500.00"),
            cost_basis=None,
            currency="USD",
        ),
    ]

    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc, as_of=snap_date, positions=tuple(positions)),
        ],
        started_at=now,
        completed_at=now,
        snapshot_date=snap_date,
    )

    service = OverviewService(repository)
    overview = service.get_overview()

    assert overview.total_known_usd_value == Decimal("1500.00")

    # Allocations
    alloc = overview.allocations["asset_class"]
    assert alloc.denominator == Decimal("1500.00")
    assert alloc.included_count == 2
    assert alloc.excluded_count == 1
    assert len(alloc.slices) == 1
    assert alloc.slices[0].amount == Decimal("1500.00")
    assert alloc.slices[0].percentage == Decimal("1.0000")
    assert alloc.slices[0].position_count == 2

    # Gain loss
    assert overview.gain_loss.cost_basis == Decimal("800.00")
    assert overview.gain_loss.market_value == Decimal("1000.00")
    assert overview.gain_loss.unrealized_gain_loss == Decimal("200.00")
    assert overview.gain_loss.included_count == 1
    assert overview.gain_loss.excluded_count == 2

    # Exclusions
    reasons = {e.reason: e for e in overview.exclusions}
    assert "missing_market_value" in reasons
    assert reasons["missing_market_value"].symbol == "PRIV"
    assert reasons["missing_market_value"].account_id == "acc-test"

    assert "missing_cost_basis" in reasons
    assert reasons["missing_cost_basis"].symbol == "GIFT"
    assert reasons["missing_cost_basis"].account_id == "acc-test"


def test_non_usd_holdings_excluded_and_reported(tmp_path) -> None:
    from portfolio_mcp.models import Account, HoldingsSnapshot, Position

    database_url = f"sqlite:///{tmp_path / 'non_usd.db'}"
    repository = PortfolioRepository(database_url)

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    snap_date = date(2026, 8, 29)

    acc = Account(
        id="acc-intl",
        provider="InteractiveBrokers",
        label="IBKR Global",
        account_type="taxable_brokerage",
        currency="USD",
    )
    positions = [
        Position(
            account_id="acc-intl",
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class="equity_etf",
            quantity=Decimal("10"),
            current_price=Decimal("300.00"),
            market_value=Decimal("3000.00"),
            cost_basis=Decimal("2700.00"),
            currency="USD",
        ),
        Position(
            account_id="acc-intl",
            symbol="SHOP.TO",
            name="Shopify Inc.",
            asset_class="equity",
            quantity=Decimal("15"),
            current_price=Decimal("100.00"),
            market_value=Decimal("1500.00"),
            cost_basis=Decimal("1200.00"),
            currency="CAD",
        ),
        Position(
            account_id="acc-intl",
            symbol="SAP.DE",
            name="SAP SE",
            asset_class="equity",
            quantity=Decimal("10"),
            current_price=Decimal("200.00"),
            market_value=Decimal("2000.00"),
            cost_basis=Decimal("1800.00"),
            currency="EUR",
        ),
    ]

    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc, as_of=snap_date, positions=tuple(positions)),
        ],
        started_at=now,
        completed_at=now,
        snapshot_date=snap_date,
    )

    service = OverviewService(repository)
    overview = service.get_overview()

    assert overview.total_known_usd_value == Decimal("3000.00")

    alloc = overview.allocations["asset_class"]
    assert alloc.denominator == Decimal("3000.00")
    assert alloc.included_count == 1
    assert alloc.excluded_count == 2

    assert overview.gain_loss.market_value == Decimal("3000.00")
    assert overview.gain_loss.cost_basis == Decimal("2700.00")
    assert overview.gain_loss.unrealized_gain_loss == Decimal("300.00")
    assert overview.gain_loss.included_count == 1
    assert overview.gain_loss.excluded_count == 2

    non_usd_exclusions = [
        e for e in overview.exclusions if e.reason == "unsupported_currency"
    ]
    assert len(non_usd_exclusions) == 2
    excl_map = {e.symbol: e for e in non_usd_exclusions}
    assert "SHOP.TO" in excl_map
    assert "CAD" in excl_map["SHOP.TO"].details
    assert excl_map["SHOP.TO"].account_id == "acc-intl"

    assert "SAP.DE" in excl_map
    assert "EUR" in excl_map["SAP.DE"].details
    assert excl_map["SAP.DE"].account_id == "acc-intl"


def test_partial_refresh_and_stale_accounts(tmp_path) -> None:
    from portfolio_mcp.models import Account, HoldingsSnapshot, Position

    database_url = f"sqlite:///{tmp_path / 'stale.db'}"
    repository = PortfolioRepository(database_url)

    t1 = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 29, 14, 0, tzinfo=UTC)
    d = date(2026, 8, 29)

    acc1 = Account(
        id="acc-schwab",
        provider="Schwab",
        label="Schwab Taxable",
        account_type="taxable_brokerage",
        currency="USD",
    )
    pos1 = [
        Position(
            account_id="acc-schwab",
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class="equity_etf",
            quantity=Decimal("10"),
            current_price=Decimal("300.00"),
            market_value=Decimal("3000.00"),
            cost_basis=Decimal("2500.00"),
            currency="USD",
        ),
    ]

    acc2 = Account(
        id="acc-fidelity",
        provider="Fidelity",
        label="Fidelity Roth",
        account_type="roth_ira",
        currency="USD",
    )
    pos2 = [
        Position(
            account_id="acc-fidelity",
            symbol="BND",
            name="Vanguard Total Bond Market ETF",
            asset_class="bond_etf",
            quantity=Decimal("25"),
            current_price=Decimal("80.00"),
            market_value=Decimal("2000.00"),
            cost_basis=Decimal("1900.00"),
            currency="USD",
        ),
    ]

    # Initial full refresh
    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc1, as_of=d, positions=tuple(pos1)),
            HoldingsSnapshot(account=acc2, as_of=d, positions=tuple(pos2)),
        ],
        started_at=t1,
        completed_at=t1,
        snapshot_date=d,
    )

    # Second refresh: Fidelity fails, Schwab succeeds
    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc1, as_of=d, positions=tuple(pos1)),
        ],
        started_at=t2,
        completed_at=t2,
        snapshot_date=d,
        failed_accounts=[acc2],
    )

    service = OverviewService(repository)
    overview = service.get_overview()

    assert overview.status == "partial"
    assert overview.total_known_usd_value == Decimal("5000.00")

    acc_map = {a.account_id: a for a in overview.accounts}
    assert acc_map["acc-schwab"].is_stale is False
    assert acc_map["acc-fidelity"].is_stale is True
    assert acc_map["acc-fidelity"].market_value == Decimal("2000.00")

    assert len(overview.warnings) > 0
    assert any("Fidelity" in w or "stale" in w.lower() for w in overview.warnings)


def test_failed_refresh_all_accounts_stale(tmp_path) -> None:
    from portfolio_mcp.models import Account, HoldingsSnapshot, Position

    database_url = f"sqlite:///{tmp_path / 'all_stale.db'}"
    repository = PortfolioRepository(database_url)

    t1 = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 29, 14, 0, tzinfo=UTC)
    d = date(2026, 8, 29)

    acc = Account(
        id="acc-1",
        provider="Schwab",
        label="Schwab Brokerage",
        account_type="taxable_brokerage",
        currency="USD",
    )
    pos = [
        Position(
            account_id="acc-1",
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class="equity_etf",
            quantity=Decimal("10"),
            current_price=Decimal("300.00"),
            market_value=Decimal("3000.00"),
            cost_basis=Decimal("2500.00"),
            currency="USD",
        ),
    ]

    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc, as_of=d, positions=tuple(pos)),
        ],
        started_at=t1,
        completed_at=t1,
        snapshot_date=d,
    )

    # Now entire provider fails
    repository.save_failed_refresh(
        started_at=t2,
        completed_at=t2,
        error_code="provider_error",
        error_message="Schwab API unavailable",
    )

    service = OverviewService(repository)
    overview = service.get_overview()

    assert overview.status == "stale"
    assert overview.total_known_usd_value == Decimal("3000.00")
    assert len(overview.accounts) == 1
    assert overview.accounts[0].is_stale is True
    assert len(overview.warnings) > 0
    assert any("stale" in w.lower() or "Schwab" in w for w in overview.warnings)


def test_daily_value_history_aggregation_and_gap_preservation(tmp_path) -> None:
    from portfolio_mcp.database import DailyAccountValueRecord
    from portfolio_mcp.models import Account, HoldingsSnapshot, Position

    database_url = f"sqlite:///{tmp_path / 'history.db'}"
    repository = PortfolioRepository(database_url)

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    snap_date = date(2026, 8, 29)

    acc1 = Account(
        id="acc-1",
        provider="Schwab",
        label="Schwab 1",
        account_type="taxable_brokerage",
        currency="USD",
    )
    acc2 = Account(
        id="acc-2",
        provider="Fidelity",
        label="Fidelity 2",
        account_type="roth_ira",
        currency="USD",
    )
    acc3 = Account(
        id="acc-3",
        provider="Questrade",
        label="Questrade CAD",
        account_type="taxable_brokerage",
        currency="CAD",
    )

    pos = [
        Position(
            account_id="acc-1",
            symbol="VTI",
            name="VTI",
            asset_class="equity_etf",
            quantity=Decimal("1"),
            current_price=Decimal("100.00"),
            market_value=Decimal("100.00"),
            cost_basis=Decimal("90.00"),
            currency="USD",
        )
    ]

    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc1, as_of=snap_date, positions=tuple(pos)),
            HoldingsSnapshot(account=acc2, as_of=snap_date, positions=()),
            HoldingsSnapshot(account=acc3, as_of=snap_date, positions=()),
        ],
        started_at=now,
        completed_at=now,
        snapshot_date=snap_date,
    )

    # Insert daily snapshots directly
    with repository._sessions.begin() as session:
        # 2026-08-01: acc1 (5000 USD), acc2 (3000 USD), acc3 (2500 CAD)
        session.add(
            DailyAccountValueRecord(
                account_id="acc-1",
                snapshot_date=date(2026, 8, 1),
                value=Decimal("5000.00"),
                currency="USD",
                recorded_at=now,
            )
        )
        session.add(
            DailyAccountValueRecord(
                account_id="acc-2",
                snapshot_date=date(2026, 8, 1),
                value=Decimal("3000.00"),
                currency="USD",
                recorded_at=now,
            )
        )
        session.add(
            DailyAccountValueRecord(
                account_id="acc-3",
                snapshot_date=date(2026, 8, 1),
                value=Decimal("2500.00"),
                currency="CAD",
                recorded_at=now,
            )
        )

        # 2026-08-02: acc1 (5100 USD), acc2 (3100 USD)
        session.add(
            DailyAccountValueRecord(
                account_id="acc-1",
                snapshot_date=date(2026, 8, 2),
                value=Decimal("5100.00"),
                currency="USD",
                recorded_at=now,
            )
        )
        session.add(
            DailyAccountValueRecord(
                account_id="acc-2",
                snapshot_date=date(2026, 8, 2),
                value=Decimal("3100.00"),
                currency="USD",
                recorded_at=now,
            )
        )

        # Gap from 2026-08-03 to 2026-08-09 has NO records

        # 2026-08-10: acc1 (5200 USD), acc2 (3200 USD)
        session.add(
            DailyAccountValueRecord(
                account_id="acc-1",
                snapshot_date=date(2026, 8, 10),
                value=Decimal("5200.00"),
                currency="USD",
                recorded_at=now,
            )
        )
        session.add(
            DailyAccountValueRecord(
                account_id="acc-2",
                snapshot_date=date(2026, 8, 10),
                value=Decimal("3200.00"),
                currency="USD",
                recorded_at=now,
            )
        )

        # 2026-08-11: only acc2 (3300 USD)
        session.add(
            DailyAccountValueRecord(
                account_id="acc-2",
                snapshot_date=date(2026, 8, 11),
                value=Decimal("3300.00"),
                currency="USD",
                recorded_at=now,
            )
        )

    service = OverviewService(repository)
    overview = service.get_overview()

    history = overview.history
    # 4 inserted dates + 1 date automatically saved by save_refresh (2026-08-29)
    assert len(history.points) == 5
    assert "CAD" in history.currencies
    assert "USD" in history.currencies

    pt1, pt2, pt3, pt4, pt5 = history.points
    assert pt1.snapshot_date == date(2026, 8, 1)
    assert pt1.value == Decimal("8000.00")
    assert pt1.currency == "USD"
    assert pt1.accounts_count == 2

    assert pt2.snapshot_date == date(2026, 8, 2)
    assert pt2.value == Decimal("8200.00")
    assert pt2.currency == "USD"
    assert pt2.accounts_count == 2

    # Gap preserved: no point for 2026-08-03 through 09
    assert pt3.snapshot_date == date(2026, 8, 10)
    assert pt3.value == Decimal("8400.00")
    assert pt3.currency == "USD"
    assert pt3.accounts_count == 2

    assert pt4.snapshot_date == date(2026, 8, 11)
    assert pt4.value == Decimal("3300.00")
    assert pt4.currency == "USD"
    assert pt4.accounts_count == 1

    # Gap preserved: no point for 2026-08-12 through 28
    assert pt5.snapshot_date == date(2026, 8, 29)
    assert pt5.value == Decimal("100.00")
    assert pt5.currency == "USD"
    assert pt5.accounts_count == 1


def test_overview_to_dict_serialization(tmp_path) -> None:
    from portfolio_mcp.models import Account, HoldingsSnapshot, Position

    database_url = f"sqlite:///{tmp_path / 'dict.db'}"
    repository = PortfolioRepository(database_url)

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    snap_date = date(2026, 8, 29)

    acc = Account(
        id="acc-1",
        provider="Schwab",
        label="Schwab 1",
        account_type="taxable_brokerage",
        currency="USD",
    )
    pos = [
        Position(
            account_id="acc-1",
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class="equity_etf",
            quantity=Decimal("10"),
            current_price=Decimal("300.00"),
            market_value=Decimal("3000.00"),
            cost_basis=Decimal("2500.00"),
            currency="USD",
        )
    ]

    repository.save_refresh(
        snapshots=[
            HoldingsSnapshot(account=acc, as_of=snap_date, positions=tuple(pos))
        ],
        started_at=now,
        completed_at=now,
        snapshot_date=snap_date,
    )

    service = OverviewService(repository)
    data = service.get_overview().to_dict()

    assert data["total_known_usd_value"] == "3000.00"
    assert data["status"] == "fresh"
    assert data["as_of"] == "2026-08-29"
    assert data["refreshed_at"] == now.isoformat()
    assert isinstance(data["accounts"], list)
    accounts = data["accounts"]
    assert isinstance(accounts, list)
    acc0 = accounts[0]
    assert isinstance(acc0, dict)
    assert acc0["market_value"] == "3000.00"
    assert acc0["percentage_of_total"] == "1.0000"

    allocations = data["allocations"]
    assert isinstance(allocations, dict)
    account_alloc = allocations["account"]
    assert isinstance(account_alloc, dict)
    assert account_alloc["denominator"] == "3000.00"
    asset_alloc = allocations["asset_class"]
    assert isinstance(asset_alloc, dict)
    assert asset_alloc["denominator"] == "3000.00"

    gain_loss = data["gain_loss"]
    assert isinstance(gain_loss, dict)
    assert gain_loss["unrealized_gain_loss"] == "500.00"
    assert gain_loss["market_value"] == "3000.00"
    assert gain_loss["cost_basis"] == "2500.00"

    history = data["history"]
    assert isinstance(history, dict)
    assert isinstance(history["points"], list)
