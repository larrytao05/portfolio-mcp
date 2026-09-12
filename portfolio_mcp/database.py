from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic.config import Config
from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    delete,
    select,
)
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from alembic import command
from portfolio_mcp.models import Account, HoldingsSnapshot


class Base(DeclarativeBase):
    pass


class UtcTimestamp(TypeDecorator[datetime]):
    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat()

    def process_result_value(
        self, value: str | None, dialect: object
    ) -> datetime | None:
        return datetime.fromisoformat(value) if value is not None else None


class AccountRecord(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    account_type: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)


class PositionRecord(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class DailyAccountValueRecord(Base):
    __tablename__ = "daily_account_values"
    __table_args__ = (UniqueConstraint("account_id", "snapshot_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)


class RefreshRunRecord(Base):
    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    account_count: Mapped[int] = mapped_column(Integer, nullable=False)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False)


def create_engine_for(database_url: str) -> Engine:
    return create_engine(database_url)


def upgrade_database(database_url: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


class PortfolioRepository:
    def __init__(self, database_url: str) -> None:
        upgrade_database(database_url)
        self._sessions = sessionmaker(
            create_engine_for(database_url), expire_on_commit=False
        )

    def list_accounts(self) -> list[Account]:
        with self._sessions() as session:
            records = session.scalars(
                select(AccountRecord).order_by(AccountRecord.label)
            )
            return [
                Account(
                    id=record.id,
                    provider=record.provider,
                    label=record.label,
                    account_type=record.account_type,
                    currency=record.currency,
                )
                for record in records
            ]

    def save_refresh(
        self,
        snapshots: list[HoldingsSnapshot],
        started_at: datetime,
        completed_at: datetime,
        snapshot_date: date,
    ) -> "RefreshResult":
        with self._sessions.begin() as session:
            for snapshot in snapshots:
                self._save_snapshot(session, snapshot, completed_at, snapshot_date)

            run = RefreshRunRecord(
                started_at=started_at,
                completed_at=completed_at,
                status="success",
                account_count=len(snapshots),
                position_count=sum(len(snapshot.positions) for snapshot in snapshots),
                snapshot_count=sum(
                    any(
                        position.market_value is not None
                        for position in snapshot.positions
                    )
                    for snapshot in snapshots
                ),
            )
            session.add(run)
            session.flush()
            return RefreshResult(
                id=run.id,
                status=run.status,
                started_at=run.started_at,
                completed_at=run.completed_at,
                account_count=run.account_count,
                position_count=run.position_count,
                snapshot_count=run.snapshot_count,
            )

    def daily_values(self, account_id: str) -> list[DailyAccountValueRecord]:
        with self._sessions() as session:
            return list(
                session.scalars(
                    select(DailyAccountValueRecord)
                    .where(DailyAccountValueRecord.account_id == account_id)
                    .order_by(DailyAccountValueRecord.snapshot_date)
                )
            )

    def _save_snapshot(
        self,
        session: Session,
        snapshot: HoldingsSnapshot,
        refreshed_at: datetime,
        snapshot_date: date,
    ) -> None:
        account = session.get(AccountRecord, snapshot.account.id)
        if account is None:
            account = AccountRecord(id=snapshot.account.id)
            session.add(account)
        account.provider = snapshot.account.provider
        account.label = snapshot.account.label
        account.account_type = snapshot.account.account_type
        account.currency = snapshot.account.currency
        account.refreshed_at = refreshed_at

        session.execute(
            delete(PositionRecord).where(
                PositionRecord.account_id == snapshot.account.id
            )
        )
        session.add_all(
            [
                PositionRecord(
                    account_id=position.account_id,
                    symbol=position.symbol,
                    name=position.name,
                    asset_class=position.asset_class,
                    quantity=position.quantity,
                    current_price=position.current_price,
                    market_value=position.market_value,
                    cost_basis=position.cost_basis,
                    currency=position.currency,
                    as_of=snapshot.as_of,
                )
                for position in snapshot.positions
            ]
        )

        values = [
            position.market_value
            for position in snapshot.positions
            if position.market_value is not None
        ]
        if not values:
            return

        daily_value = session.scalar(
            select(DailyAccountValueRecord).where(
                DailyAccountValueRecord.account_id == snapshot.account.id,
                DailyAccountValueRecord.snapshot_date == snapshot_date,
            )
        )
        if daily_value is None:
            daily_value = DailyAccountValueRecord(
                account_id=snapshot.account.id,
                snapshot_date=snapshot_date,
            )
            session.add(daily_value)
        daily_value.value = sum(values, start=Decimal("0"))
        daily_value.currency = snapshot.account.currency
        daily_value.recorded_at = refreshed_at


class RefreshResult:
    def __init__(
        self,
        id: int,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        account_count: int,
        position_count: int,
        snapshot_count: int,
    ) -> None:
        self.id = id
        self.status = status
        self.started_at = started_at
        self.completed_at = completed_at
        self.account_count = account_count
        self.position_count = position_count
        self.snapshot_count = snapshot_count

    def to_dict(self) -> dict[str, int | str]:
        return {
            "id": self.id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "accounts_refreshed": self.account_count,
            "positions_refreshed": self.position_count,
            "daily_snapshots_recorded": self.snapshot_count,
        }
