from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic.config import Config
from sqlalchemy import (
    Boolean,
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
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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


class RefreshProviderOutcomeRecord(Base):
    __tablename__ = "refresh_provider_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    refresh_id: Mapped[int] = mapped_column(
        ForeignKey("refresh_runs.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    accounts_refreshed: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    warning: Mapped[str | None] = mapped_column(String(256))


@dataclass(frozen=True)
class StoredPosition:
    position: Position
    as_of: date
    is_stale: bool
    source_refreshed_at: datetime

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "account_id": self.position.account_id,
            "as_of": self.as_of.isoformat(),
            "is_stale": self.is_stale,
            "source_refreshed_at": self.source_refreshed_at.isoformat(),
            **self.position.to_dict(),
        }


@dataclass(frozen=True)
class StoredAccount:
    account: Account
    is_stale: bool
    source_refreshed_at: datetime

    def to_dict(self) -> dict[str, str | bool]:
        return {
            **self.account.to_dict(),
            "is_stale": self.is_stale,
            "source_refreshed_at": self.source_refreshed_at.isoformat(),
        }


@dataclass(frozen=True)
class ProviderRefreshOutcome:
    provider: str
    status: str
    accounts_refreshed: int
    stale_accounts: int
    excluded_accounts: int
    warning: str | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "provider": self.provider,
            "status": self.status,
            "accounts_refreshed": self.accounts_refreshed,
            "stale_accounts": self.stale_accounts,
            "excluded_accounts": self.excluded_accounts,
            "warning": self.warning,
        }


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
    provider_outcomes: tuple[ProviderRefreshOutcome, ...] = ()

    def to_dict(self) -> dict[str, object]:
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
            "provider_outcomes": [
                outcome.to_dict() for outcome in self.provider_outcomes
            ],
            "warnings": [
                outcome.warning
                for outcome in self.provider_outcomes
                if outcome.warning is not None
            ],
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

    def list_accounts(self) -> list[StoredAccount]:
        with self._sessions() as session:
            records = session.scalars(
                select(AccountRecord).order_by(AccountRecord.label)
            )
            return [
                StoredAccount(
                    account=Account(
                        id=record.id,
                        provider=record.provider,
                        label=record.label,
                        account_type=record.account_type,
                        currency=record.currency,
                    ),
                    is_stale=record.is_stale,
                    source_refreshed_at=record.refreshed_at,
                )
                for record in records
            ]

    def list_positions(self, account_id: str) -> list[StoredPosition] | None:
        with self._sessions() as session:
            account = session.get(AccountRecord, account_id)
            if account is None:
                return None
            records = session.scalars(
                select(PositionRecord)
                .where(PositionRecord.account_id == account_id)
                .order_by(PositionRecord.symbol)
            )
            return [self._stored_position(record, account) for record in records]

    def save_refresh(
        self,
        snapshots: list[HoldingsSnapshot],
        started_at: datetime,
        completed_at: datetime,
        snapshot_date: date,
        failed_accounts: list[Account] | None = None,
    ) -> "RefreshResult":
        failed_accounts = failed_accounts or []
        with self._sessions.begin() as session:
            for snapshot in snapshots:
                self._save_snapshot(session, snapshot, completed_at, snapshot_date)

            stale_account_ids = self._mark_failed_accounts(session, failed_accounts)
            outcomes = self._provider_outcomes(
                snapshots, failed_accounts, stale_account_ids
            )
            status = (
                "success"
                if not failed_accounts
                else "partial"
                if snapshots
                else "failed"
            )

            run = RefreshRunRecord(
                started_at=started_at,
                completed_at=completed_at,
                status=status,
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
            self._save_outcomes(session, run.id, outcomes)
            return self._refresh_result(run, outcomes)

    def save_failed_refresh(
        self,
        started_at: datetime,
        completed_at: datetime,
        error_code: str,
        error_message: str,
    ) -> RefreshResult:
        with self._sessions.begin() as session:
            stale_accounts = self._mark_all_accounts_stale(session)
            outcomes = self._failed_outcomes(stale_accounts)
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
            self._save_outcomes(session, run.id, outcomes)
            return self._refresh_result(run, outcomes)

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
            if record is None:
                return None
            return self._refresh_result(
                record, self._outcomes_for_refresh(session, record.id)
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
        account.is_stale = False

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

    def _mark_failed_accounts(
        self, session: Session, failed_accounts: list[Account]
    ) -> set[str]:
        stale_account_ids: set[str] = set()
        for account in failed_accounts:
            record = session.get(AccountRecord, account.id)
            if record is None:
                continue
            record.is_stale = True
            stale_account_ids.add(account.id)
        return stale_account_ids

    def _mark_all_accounts_stale(self, session: Session) -> list[AccountRecord]:
        records = list(session.scalars(select(AccountRecord)))
        for record in records:
            record.is_stale = True
        return records

    def _provider_outcomes(
        self,
        snapshots: list[HoldingsSnapshot],
        failed_accounts: list[Account],
        stale_account_ids: set[str],
    ) -> tuple[ProviderRefreshOutcome, ...]:
        counts: dict[str, dict[str, int]] = {}
        for snapshot in snapshots:
            provider = counts.setdefault(
                snapshot.account.provider,
                {"refreshed": 0, "stale": 0, "excluded": 0},
            )
            provider["refreshed"] += 1
        for account in failed_accounts:
            provider = counts.setdefault(
                account.provider, {"refreshed": 0, "stale": 0, "excluded": 0}
            )
            key = "stale" if account.id in stale_account_ids else "excluded"
            provider[key] += 1

        outcomes: list[ProviderRefreshOutcome] = []
        for provider, count in sorted(counts.items()):
            has_failures = count["stale"] or count["excluded"]
            status = (
                "partial"
                if count["refreshed"] and has_failures
                else "failed"
                if has_failures
                else "success"
            )
            outcomes.append(
                ProviderRefreshOutcome(
                    provider=provider,
                    status=status,
                    accounts_refreshed=count["refreshed"],
                    stale_accounts=count["stale"],
                    excluded_accounts=count["excluded"],
                    warning=self._outcome_warning(
                        provider, count["stale"], count["excluded"]
                    ),
                )
            )
        return tuple(outcomes)

    def _failed_outcomes(
        self, stale_accounts: list[AccountRecord]
    ) -> tuple[ProviderRefreshOutcome, ...]:
        if not stale_accounts:
            return (
                ProviderRefreshOutcome(
                    provider="Portfolio provider",
                    status="failed",
                    accounts_refreshed=0,
                    stale_accounts=0,
                    excluded_accounts=0,
                    warning="Portfolio data is unavailable and excluded from totals.",
                ),
            )
        counts: dict[str, int] = {}
        for account in stale_accounts:
            counts[account.provider] = counts.get(account.provider, 0) + 1
        return tuple(
            ProviderRefreshOutcome(
                provider=provider,
                status="failed",
                accounts_refreshed=0,
                stale_accounts=count,
                excluded_accounts=0,
                warning=f"{provider} data is stale; last successful data is shown.",
            )
            for provider, count in sorted(counts.items())
        )

    def _outcome_warning(
        self, provider: str, stale_accounts: int, excluded_accounts: int
    ) -> str | None:
        if stale_accounts and excluded_accounts:
            return (
                f"{provider} has stale data and unavailable accounts excluded from "
                "totals."
            )
        if stale_accounts:
            return f"{provider} data is stale; last successful data is shown."
        if excluded_accounts:
            return f"{provider} data is unavailable and excluded from totals."
        return None

    def _save_outcomes(
        self,
        session: Session,
        refresh_id: int,
        outcomes: tuple[ProviderRefreshOutcome, ...],
    ) -> None:
        session.add_all(
            [
                RefreshProviderOutcomeRecord(
                    refresh_id=refresh_id,
                    provider=outcome.provider,
                    status=outcome.status,
                    accounts_refreshed=outcome.accounts_refreshed,
                    stale_accounts=outcome.stale_accounts,
                    excluded_accounts=outcome.excluded_accounts,
                    warning=outcome.warning,
                )
                for outcome in outcomes
            ]
        )

    def _outcomes_for_refresh(
        self, session: Session, refresh_id: int
    ) -> tuple[ProviderRefreshOutcome, ...]:
        records = session.scalars(
            select(RefreshProviderOutcomeRecord)
            .where(RefreshProviderOutcomeRecord.refresh_id == refresh_id)
            .order_by(RefreshProviderOutcomeRecord.provider)
        )
        return tuple(
            ProviderRefreshOutcome(
                provider=record.provider,
                status=record.status,
                accounts_refreshed=record.accounts_refreshed,
                stale_accounts=record.stale_accounts,
                excluded_accounts=record.excluded_accounts,
                warning=record.warning,
            )
            for record in records
        )

    def _stored_position(
        self, record: PositionRecord, account: AccountRecord
    ) -> StoredPosition:
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
            is_stale=account.is_stale,
            source_refreshed_at=account.refreshed_at,
        )

    def _refresh_result(
        self,
        record: RefreshRunRecord,
        outcomes: tuple[ProviderRefreshOutcome, ...] = (),
    ) -> RefreshResult:
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
            provider_outcomes=outcomes,
        )
