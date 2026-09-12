from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic.config import Config
from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    select,
)
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from alembic import command
from portfolio_mcp.models import Account, HoldingsSnapshot, Position


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


class ExactDecimal(TypeDecorator[Decimal]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: object) -> str | None:
        return str(value) if value is not None else None

    def process_result_value(
        self, value: str | None, dialect: object
    ) -> Decimal | None:
        return Decimal(value) if value is not None else None


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
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    market_value: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    cost_basis: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class DailyAccountValueRecord(Base):
    __tablename__ = "daily_account_values"
    __table_args__ = (UniqueConstraint("account_id", "snapshot_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
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
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(256))


@dataclass(frozen=True)
class StoredPosition:
    position: Position
    as_of: date

    def to_dict(self) -> dict[str, str | None]:
        gain_loss = (
            self.position.market_value - self.position.cost_basis
            if self.position.market_value is not None
            and self.position.cost_basis is not None
            else None
        )
        return {
            "account_id": self.position.account_id,
            "as_of": self.as_of.isoformat(),
            **self.position.to_dict(),
            "gain_loss": str(gain_loss) if gain_loss is not None else None,
        }


@dataclass(frozen=True)
class AccountDetail:
    account: Account
    refreshed_at: datetime
    as_of: date | None
    positions: tuple[StoredPosition, ...]

    def to_dict(self) -> dict[str, object]:
        market_values = [
            stored.position.market_value
            for stored in self.positions
            if stored.position.market_value is not None
        ]
        cost_bases = [
            stored.position.cost_basis
            for stored in self.positions
            if stored.position.cost_basis is not None
        ]
        currencies_match = all(
            stored.position.currency == self.account.currency
            for stored in self.positions
        )
        return {
            "id": self.account.id,
            "provider": self.account.provider,
            "label": self.account.label,
            "account_type": self.account.account_type,
            "currency": self.account.currency,
            "refreshed_at": self.refreshed_at.isoformat(),
            "as_of": self.as_of.isoformat() if self.as_of is not None else None,
            "balances": {
                "market_value": self._total_if_complete(
                    market_values, currencies_match
                ),
                "cost_basis": self._total_if_complete(cost_bases, currencies_match),
                "currency": self.account.currency,
            },
            "positions": [position.to_dict() for position in self.positions],
        }

    def _total_if_complete(
        self, values: list[Decimal], currencies_match: bool
    ) -> str | None:
        if not currencies_match or len(values) != len(self.positions):
            return None
        return str(sum(values, start=Decimal("0")))


@dataclass(frozen=True)
class DailyAccountValue:
    account_id: str
    snapshot_date: date
    value: Decimal
    currency: str
    recorded_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {
            "account_id": self.account_id,
            "snapshot_date": self.snapshot_date.isoformat(),
            "value": str(self.value),
            "currency": self.currency,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass(frozen=True)
class RefreshResult:
    id: int
    status: str
    started_at: datetime
    completed_at: datetime
    account_count: int
    position_count: int
    snapshot_count: int
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "id": self.id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "accounts_refreshed": self.account_count,
            "positions_refreshed": self.position_count,
            "daily_snapshots_recorded": self.snapshot_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


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

    def list_positions(self, account_id: str) -> list[StoredPosition] | None:
        with self._sessions() as session:
            if session.get(AccountRecord, account_id) is None:
                return None
            records = session.scalars(
                select(PositionRecord)
                .where(PositionRecord.account_id == account_id)
                .order_by(PositionRecord.symbol)
            )
            return [self._stored_position(record) for record in records]

    def account_detail(self, account_id: str) -> AccountDetail | None:
        with self._sessions() as session:
            account = session.get(AccountRecord, account_id)
            if account is None:
                return None
            records = list(
                session.scalars(
                    select(PositionRecord)
                    .where(PositionRecord.account_id == account_id)
                    .order_by(PositionRecord.symbol)
                )
            )
            positions = tuple(self._stored_position(record) for record in records)
            return AccountDetail(
                account=Account(
                    id=account.id,
                    provider=account.provider,
                    label=account.label,
                    account_type=account.account_type,
                    currency=account.currency,
                ),
                refreshed_at=account.refreshed_at,
                as_of=positions[0].as_of if positions else None,
                positions=positions,
            )

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

    def save_failed_refresh(
        self,
        started_at: datetime,
        completed_at: datetime,
        error_code: str,
        error_message: str,
    ) -> RefreshResult:
        with self._sessions.begin() as session:
            run = RefreshRunRecord(
                started_at=started_at,
                completed_at=completed_at,
                status="failed",
                account_count=0,
                position_count=0,
                snapshot_count=0,
                error_code=error_code,
                error_message=error_message,
            )
            session.add(run)
            session.flush()
            return self._refresh_result(run)

    def daily_values(self, account_id: str) -> list[DailyAccountValue] | None:
        with self._sessions() as session:
            if session.get(AccountRecord, account_id) is None:
                return None
            records = session.scalars(
                select(DailyAccountValueRecord)
                .where(DailyAccountValueRecord.account_id == account_id)
                .order_by(DailyAccountValueRecord.snapshot_date)
            )
            return [
                DailyAccountValue(
                    account_id=record.account_id,
                    snapshot_date=record.snapshot_date,
                    value=record.value,
                    currency=record.currency,
                    recorded_at=record.recorded_at,
                )
                for record in records
            ]

    def latest_refresh(self) -> RefreshResult | None:
        with self._sessions() as session:
            record = session.scalar(
                select(RefreshRunRecord).order_by(RefreshRunRecord.id.desc()).limit(1)
            )
            return self._refresh_result(record) if record is not None else None

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

    def _stored_position(self, record: PositionRecord) -> StoredPosition:
        return StoredPosition(
            position=Position(
                account_id=record.account_id,
                symbol=record.symbol,
                name=record.name,
                asset_class=record.asset_class,
                quantity=record.quantity,
                current_price=record.current_price,
                market_value=record.market_value,
                cost_basis=record.cost_basis,
                currency=record.currency,
            ),
            as_of=record.as_of,
        )

    def _refresh_result(self, record: RefreshRunRecord) -> RefreshResult:
        return RefreshResult(
            id=record.id,
            status=record.status,
            started_at=record.started_at,
            completed_at=record.completed_at,
            account_count=record.account_count,
            position_count=record.position_count,
            snapshot_count=record.snapshot_count,
            error_code=record.error_code,
            error_message=record.error_message,
        )
