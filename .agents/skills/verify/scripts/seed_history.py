#!/usr/bin/env python3
import asyncio
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from portfolio_mcp.connection_store import ConnectionStore
from portfolio_mcp.database import Database
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.portfolio_store import save_refresh


async def seed(database_path: Path):
    if database_path.exists():
        raise ValueError("History seeding requires a new database.")
    provider = FixturePortfolioProvider()
    database = Database(f"sqlite:///{database_path}")
    routes = ConnectionStore(database)
    routes.save_connection("fixture", "fixture", "fixture", "fixture", True)
    snapshots = [
        await provider.get_holdings(account.id)
        for account in await provider.list_accounts()
    ]
    for snapshot in snapshots:
        routes.save_account_route(snapshot.account.id, "fixture", snapshot.account.id)
    now = datetime.now(ZoneInfo("America/New_York"))
    for offset in range(45, 0, -1):
        if offset in {10, 11}:
            continue
        saved = now - timedelta(days=offset)
        snapshot_date = saved.date()
        factor = Decimal(1000 - offset + offset % 7) / Decimal(1000)
        daily = [
            replace(
                snapshot,
                as_of=snapshot_date,
                positions=tuple(
                    replace(
                        position,
                        market_value=(position.market_value * factor).quantize(
                            Decimal(".01")
                        )
                        if position.market_value is not None
                        else None,
                        current_price=(position.current_price * factor).quantize(
                            Decimal(".01")
                        )
                        if position.current_price is not None
                        else None,
                    )
                    for position in snapshot.positions
                ),
            )
            for snapshot in snapshots
        ]
        if offset == 15:
            daily = daily[:1]
        save_refresh(database, daily, saved, saved, snapshot_date=snapshot_date)


if __name__ == "__main__":
    path = Path(sys.argv[1]).resolve()
    if (
        path.parent.parent
        != Path(tempfile.gettempdir()).resolve() / "portfolio-fixture-verification"
        or path.name != "portfolio.sqlite"
    ):
        raise SystemExit("Use only a fresh run database from verify_dashboard.py.")
    asyncio.run(seed(path))
