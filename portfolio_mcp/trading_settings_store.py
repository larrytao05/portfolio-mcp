from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import (
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult

from portfolio_mcp.database import Database
from portfolio_mcp.schema import (
    TradingSettingsRecord,
)


@dataclass(frozen=True)
class StoredTradingSettings:
    live_trading_enabled: bool
    kill_switch_active: bool
    max_order_shares: Decimal | None
    max_order_notional_usd: Decimal | None
    updated_at: datetime | None
    version: int


def trading_settings(db: Database) -> StoredTradingSettings:
    with db.sessions() as session:
        record = session.get(TradingSettingsRecord, 1)
        if record is None:
            return StoredTradingSettings(False, True, None, None, None, 0)
        return _stored_trading_settings(record)


def replace_trading_settings(
    db: Database,
    *,
    live_trading_enabled: bool,
    kill_switch_active: bool,
    max_order_shares: Decimal | None,
    max_order_notional_usd: Decimal | None,
    expected_version: int,
    updated_at: datetime,
) -> StoredTradingSettings | None:
    values = {
        "live_trading_enabled": live_trading_enabled,
        "kill_switch_active": kill_switch_active,
        "max_order_shares": max_order_shares,
        "max_order_notional_usd": max_order_notional_usd,
        "updated_at": updated_at,
        "version": expected_version + 1,
    }
    with db.sessions.begin() as session:
        if expected_version == 0:
            inserted = cast(
                CursorResult[object],
                session.execute(
                    sqlite_insert(TradingSettingsRecord)
                    .values(id=1, **values)
                    .on_conflict_do_nothing(index_elements=("id",))
                ),
            )
            if inserted.rowcount == 1:
                record = session.get(TradingSettingsRecord, 1)
                assert record is not None
                return _stored_trading_settings(record)
        updated = cast(
            CursorResult[object],
            session.execute(
                update(TradingSettingsRecord)
                .where(
                    TradingSettingsRecord.id == 1,
                    TradingSettingsRecord.version == expected_version,
                )
                .values(**values)
            ),
        )
        if updated.rowcount != 1:
            return None
        record = session.get(TradingSettingsRecord, 1)
        assert record is not None
        return _stored_trading_settings(record)


def _stored_trading_settings(record: TradingSettingsRecord) -> StoredTradingSettings:
    return StoredTradingSettings(
        live_trading_enabled=record.live_trading_enabled,
        kill_switch_active=record.kill_switch_active,
        max_order_shares=record.max_order_shares,
        max_order_notional_usd=record.max_order_notional_usd,
        updated_at=record.updated_at,
        version=record.version,
    )
