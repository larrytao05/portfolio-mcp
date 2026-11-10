#!/usr/bin/env python3
import asyncio
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.fixtures import FixturePortfolioProvider


async def seed(database_path: Path):
    if database_path.exists():
        raise ValueError("History seeding requires a new database.")
    provider = FixturePortfolioProvider()
    repo = PortfolioRepository(f"sqlite:///{database_path}")
    snapshots = [
        await provider.get_holdings(account.id)
        for account in await provider.list_accounts()
    ]
    now = datetime.now(UTC)
    for offset in range(45, 0, -1):
        if offset in {10, 11}:
            continue
        saved = now - timedelta(days=offset)
        factor = Decimal(1000 - offset + offset % 7) / Decimal(1000)
        daily = [
            replace(
                snapshot,
                as_of=saved.date(),
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
        repo.save_refresh(daily, saved, saved, snapshot_date=saved.date())


if __name__ == "__main__":
    path = Path(sys.argv[1]).resolve()
    if (
        path.parent.parent
        != Path(tempfile.gettempdir()).resolve() / "portfolio-fixture-verification"
        or path.name != "portfolio.sqlite"
    ):
        raise SystemExit("Use only a fresh run database from verify_dashboard.py.")
    asyncio.run(seed(path))
