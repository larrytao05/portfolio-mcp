from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from portfolio_mcp.database import (
    DailyAccountValue,
    PortfolioRepository,
    RefreshResult,
    StoredAccount,
    StoredPosition,
)
from portfolio_mcp.models import Position


def _sum_decimals(items: Iterable[Decimal | None]) -> Decimal:
    return sum((x for x in items if x is not None), start=Decimal("0"))


def _format_label(key: str) -> str:
    return key.replace("_", " ").title()


def _derive_security_type(asset_class: str) -> str:
    normalized = asset_class.lower().replace("-", "_").strip()
    if "etf" in normalized:
        return "etf"
    if "mutual_fund" in normalized or "fund" in normalized:
        return "mutual_fund"
    if "equity" in normalized or "stock" in normalized:
        return "equity"
    if "bond" in normalized:
        return "bond"
    if "commodity" in normalized:
        return "commodity"
    if "option" in normalized:
        return "option"
    if "crypto" in normalized:
        return "crypto"
    if "cash" in normalized:
        return "cash"
    return "other"


@dataclass(frozen=True)
class OverviewAccountContribution:
    account_id: str
    label: str
    provider: str
    account_type: str
    currency: str
    market_value: Decimal | None
    is_stale: bool
    percentage_of_total: Decimal | None

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "label": self.label,
            "provider": self.provider,
            "account_type": self.account_type,
            "currency": self.currency,
            "market_value": str(self.market_value)
            if self.market_value is not None
            else None,
            "is_stale": self.is_stale,
            "percentage_of_total": str(self.percentage_of_total)
            if self.percentage_of_total is not None
            else None,
            "percentage_of_total_display": f"{self.percentage_of_total * 100:.2f}%"
            if self.percentage_of_total is not None
            else None,
        }


@dataclass(frozen=True)
class AllocationSlice:
    key: str
    label: str
    amount: Decimal
    percentage: Decimal
    position_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "amount": str(self.amount),
            "percentage": str(self.percentage),
            "percentage_display": f"{self.percentage * 100:.2f}%",
            "position_count": self.position_count,
        }


@dataclass(frozen=True)
class AllocationGroup:
    group_by: str
    denominator: Decimal
    slices: tuple[AllocationSlice, ...]
    included_count: int
    excluded_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "group_by": self.group_by,
            "denominator": str(self.denominator),
            "slices": [s.to_dict() for s in self.slices],
            "included_count": self.included_count,
            "excluded_count": self.excluded_count,
        }


@dataclass(frozen=True)
class GainLossCoverage:
    unrealized_gain_loss: Decimal | None
    cost_basis: Decimal | None
    market_value: Decimal | None
    included_count: int
    excluded_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "unrealized_gain_loss": str(self.unrealized_gain_loss)
            if self.unrealized_gain_loss is not None
            else None,
            "cost_basis": str(self.cost_basis) if self.cost_basis is not None else None,
            "market_value": str(self.market_value)
            if self.market_value is not None
            else None,
            "included_count": self.included_count,
            "excluded_count": self.excluded_count,
        }


@dataclass(frozen=True)
class OverviewExclusion:
    reason: str
    symbol: str | None
    account_id: str | None
    details: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "reason": self.reason,
            "symbol": self.symbol,
            "account_id": self.account_id,
            "details": self.details,
        }


@dataclass(frozen=True)
class DailyRecordedPoint:
    snapshot_date: date
    value: Decimal
    currency: str
    accounts_count: int
    accounts_total: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.snapshot_date.isoformat(),
            "snapshot_date": self.snapshot_date.isoformat(),
            "value": str(self.value),
            "currency": self.currency,
            "accounts_count": self.accounts_count,
            "accounts_total": self.accounts_total,
        }


@dataclass(frozen=True)
class RecordedHistory:
    points: tuple[DailyRecordedPoint, ...]
    currencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "points": [p.to_dict() for p in self.points],
            "currencies": list(self.currencies),
        }


@dataclass(frozen=True)
class PortfolioOverview:
    total_known_usd_value: Decimal | None
    cash_usd: Decimal | None
    buying_power_usd: Decimal | None
    as_of: date | None
    refreshed_at: datetime | None
    status: str
    accounts: tuple[OverviewAccountContribution, ...]
    allocations: dict[str, AllocationGroup]
    gain_loss: GainLossCoverage
    exclusions: tuple[OverviewExclusion, ...]
    warnings: tuple[str, ...]
    history: RecordedHistory

    def to_dict(self) -> dict[str, object]:
        return {
            "total_known_usd_value": str(self.total_known_usd_value)
            if self.total_known_usd_value is not None
            else None,
            "cash_usd": str(self.cash_usd) if self.cash_usd is not None else None,
            "buying_power_usd": str(self.buying_power_usd)
            if self.buying_power_usd is not None
            else None,
            "as_of": self.as_of.isoformat() if self.as_of is not None else None,
            "refreshed_at": self.refreshed_at.isoformat()
            if self.refreshed_at is not None
            else None,
            "status": self.status,
            "accounts": [a.to_dict() for a in self.accounts],
            "allocations": {k: v.to_dict() for k, v in self.allocations.items()},
            "gain_loss": self.gain_loss.to_dict(),
            "exclusions": [e.to_dict() for e in self.exclusions],
            "warnings": list(self.warnings),
            "history": self.history.to_dict(),
        }


class OverviewService:
    def __init__(self, repository: PortfolioRepository) -> None:
        self._repository = repository

    def get_overview(self) -> PortfolioOverview:
        stored_accounts = self._repository.list_accounts()
        if not stored_accounts:
            return self._empty_overview()

        stored_positions = self._repository.all_positions()
        latest_refresh = self._repository.latest_refresh()
        all_daily = self._repository.all_daily_values()

        status = self._determine_status(stored_accounts, latest_refresh)
        as_of = max((p.as_of for p in stored_positions), default=None)
        refreshed_at = (
            latest_refresh.completed_at
            if latest_refresh
            else max(a.source_refreshed_at for a in stored_accounts)
        )

        fresh_usd_positions, exclusions, gain_loss_positions = self._classify_positions(
            stored_positions, stored_accounts
        )

        all_accounts_stale = all(a.is_stale for a in stored_accounts)
        total_known_usd: Decimal | None = (
            None
            if all_accounts_stale
            else _sum_decimals(p.position.market_value for p in fresh_usd_positions)
        )

        cash_usd = self._calculate_cash_usd(fresh_usd_positions)
        account_contributions, account_slices = self._calculate_contributions(
            stored_accounts, stored_positions, fresh_usd_positions, total_known_usd
        )

        allocations = self._calculate_allocations(
            fresh_usd_positions,
            account_slices,
            total_known_usd,
            len(stored_positions) - len(fresh_usd_positions),
        )

        gain_loss = self._calculate_gain_loss(
            gain_loss_positions, len(stored_positions)
        )
        warnings = self._collect_warnings(stored_accounts, latest_refresh)
        history = self._build_history(all_daily, len(stored_accounts))

        return PortfolioOverview(
            total_known_usd_value=total_known_usd,
            cash_usd=cash_usd,
            buying_power_usd=None,
            as_of=as_of,
            refreshed_at=refreshed_at,
            status=status,
            accounts=tuple(account_contributions),
            allocations=allocations,
            gain_loss=gain_loss,
            exclusions=tuple(exclusions),
            warnings=warnings,
            history=history,
        )

    def get_history(self) -> RecordedHistory:
        accounts = self._repository.list_accounts()
        all_daily = self._repository.all_daily_values()
        return self._build_history(all_daily, len(accounts))

    def _empty_overview(self) -> PortfolioOverview:
        empty_alloc = {
            "account": AllocationGroup("account", Decimal("0"), (), 0, 0),
            "asset_class": AllocationGroup("asset_class", Decimal("0"), (), 0, 0),
            "security_type": AllocationGroup("security_type", Decimal("0"), (), 0, 0),
        }
        empty_gl = GainLossCoverage(None, None, None, 0, 0)
        return PortfolioOverview(
            total_known_usd_value=None,
            cash_usd=None,
            buying_power_usd=None,
            as_of=None,
            refreshed_at=None,
            status="empty",
            accounts=(),
            allocations=empty_alloc,
            gain_loss=empty_gl,
            exclusions=(),
            warnings=(),
            history=RecordedHistory(points=(), currencies=()),
        )

    def _determine_status(
        self,
        stored_accounts: list[StoredAccount],
        latest_refresh: RefreshResult | None,
    ) -> str:
        if all(a.is_stale for a in stored_accounts) or (
            latest_refresh and latest_refresh.status == "failed"
        ):
            return "stale"
        if any(a.is_stale for a in stored_accounts) or (
            latest_refresh and latest_refresh.status == "partial"
        ):
            return "partial"
        return "fresh"

    def _classify_positions(
        self,
        stored_positions: list[StoredPosition],
        stored_accounts: list[StoredAccount],
    ) -> tuple[list[StoredPosition], list[OverviewExclusion], list[StoredPosition]]:
        stale_account_ids = {a.account.id for a in stored_accounts if a.is_stale}
        account_labels = {a.account.id: a.account.label for a in stored_accounts}

        exclusions: list[OverviewExclusion] = []
        fresh_usd_positions: list[StoredPosition] = []
        gain_loss_positions: list[StoredPosition] = []

        for stored_pos in stored_positions:
            pos = stored_pos.position

            if pos.account_id in stale_account_ids:
                label = account_labels.get(pos.account_id, pos.account_id)
                exclusions.append(
                    OverviewExclusion(
                        reason="stale_account",
                        symbol=pos.symbol,
                        account_id=pos.account_id,
                        details=(
                            f"Holding in stale account '{label}' is excluded "
                            "from totals."
                        ),
                    )
                )
                continue

            if pos.currency != "USD":
                exclusions.append(
                    OverviewExclusion(
                        reason="unsupported_currency",
                        symbol=pos.symbol,
                        account_id=pos.account_id,
                        details=(
                            f"Holding in {pos.currency} is excluded "
                            "from USD aggregates."
                        ),
                    )
                )
                continue

            if pos.market_value is None:
                exclusions.append(
                    OverviewExclusion(
                        reason="missing_market_value",
                        symbol=pos.symbol,
                        account_id=pos.account_id,
                        details=(
                            f"Position {pos.symbol} has no market value "
                            "and is excluded from totals."
                        ),
                    )
                )
                continue

            fresh_usd_positions.append(stored_pos)

            if pos.cost_basis is None:
                exclusions.append(
                    OverviewExclusion(
                        reason="missing_cost_basis",
                        symbol=pos.symbol,
                        account_id=pos.account_id,
                        details=(
                            f"Position {pos.symbol} has no cost basis "
                            "and is excluded from unrealized gain/loss."
                        ),
                    )
                )
            else:
                gain_loss_positions.append(stored_pos)

        return fresh_usd_positions, exclusions, gain_loss_positions

    def _calculate_cash_usd(
        self, fresh_usd_positions: list[StoredPosition]
    ) -> Decimal | None:
        cash_positions = [
            p
            for p in fresh_usd_positions
            if p.position.asset_class.lower() in ("cash", "cash_equivalent")
            or p.position.symbol.upper() in ("CASH", "USD")
        ]
        if not cash_positions:
            return None
        return _sum_decimals(p.position.market_value for p in cash_positions)

    def _calculate_contributions(
        self,
        stored_accounts: list[StoredAccount],
        stored_positions: list[StoredPosition],
        fresh_usd_positions: list[StoredPosition],
        total_known_usd: Decimal | None,
    ) -> tuple[list[OverviewAccountContribution], list[AllocationSlice]]:
        account_contributions: list[OverviewAccountContribution] = []
        account_slices: list[AllocationSlice] = []

        for stored_acc in stored_accounts:
            acc = stored_acc.account
            all_acc_positions = [
                p for p in stored_positions if p.position.account_id == acc.id
            ]
            fresh_acc_positions = [
                p for p in fresh_usd_positions if p.position.account_id == acc.id
            ]

            if stored_acc.is_stale:
                # Retained last-known market value from all stored positions
                usd_positions = [
                    p for p in all_acc_positions if p.position.currency == "USD"
                ]
                acc_val = (
                    _sum_decimals(p.position.market_value for p in usd_positions)
                    if usd_positions
                    else None
                )
                pct = None
            else:
                acc_val = (
                    _sum_decimals(p.position.market_value for p in fresh_acc_positions)
                    if all_acc_positions
                    else None
                )
                if acc_val is not None and total_known_usd and total_known_usd > 0:
                    pct = (acc_val / total_known_usd).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )
                elif total_known_usd == 0:
                    pct = Decimal("0.0000")
                else:
                    pct = None

            account_contributions.append(
                OverviewAccountContribution(
                    account_id=acc.id,
                    label=acc.label,
                    provider=acc.provider,
                    account_type=acc.account_type,
                    currency=acc.currency,
                    market_value=acc_val,
                    is_stale=stored_acc.is_stale,
                    percentage_of_total=pct,
                )
            )

            if not stored_acc.is_stale and acc_val is not None and acc_val > 0:
                account_slices.append(
                    AllocationSlice(
                        key=acc.id,
                        label=acc.label,
                        amount=acc_val,
                        percentage=pct if pct is not None else Decimal("0.0000"),
                        position_count=len(fresh_acc_positions),
                    )
                )

        account_slices.sort(key=lambda s: (-s.amount, s.label))
        return account_contributions, account_slices

    def _build_allocation_group(
        self,
        group_by: str,
        positions: list[StoredPosition],
        key_fn: Callable[[Position], str],
        label_fn: Callable[[str], str],
        denominator: Decimal,
        excluded_count: int,
    ) -> AllocationGroup:
        if not positions or denominator <= 0:
            return AllocationGroup(
                group_by=group_by,
                denominator=denominator,
                slices=(),
                included_count=len(positions),
                excluded_count=excluded_count,
            )

        groups: dict[str, list[StoredPosition]] = defaultdict(list)
        for p in positions:
            key = key_fn(p.position)
            groups[key].append(p)

        slices: list[AllocationSlice] = []
        for key, pos_list in groups.items():
            amount = _sum_decimals(p.position.market_value for p in pos_list)
            pct = (amount / denominator).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            slices.append(
                AllocationSlice(
                    key=key,
                    label=label_fn(key),
                    amount=amount,
                    percentage=pct,
                    position_count=len(pos_list),
                )
            )

        slices.sort(key=lambda s: (-s.amount, s.label))
        return AllocationGroup(
            group_by=group_by,
            denominator=denominator,
            slices=tuple(slices),
            included_count=len(positions),
            excluded_count=excluded_count,
        )

    def _calculate_allocations(
        self,
        fresh_usd_positions: list[StoredPosition],
        account_slices: list[AllocationSlice],
        total_known_usd: Decimal | None,
        excluded_count: int,
    ) -> dict[str, AllocationGroup]:
        denominator = total_known_usd if total_known_usd is not None else Decimal("0")

        account_group = AllocationGroup(
            group_by="account",
            denominator=denominator,
            slices=tuple(account_slices),
            included_count=len(fresh_usd_positions),
            excluded_count=excluded_count,
        )

        asset_class_group = self._build_allocation_group(
            group_by="asset_class",
            positions=fresh_usd_positions,
            key_fn=lambda pos: pos.asset_class,
            label_fn=_format_label,
            denominator=denominator,
            excluded_count=excluded_count,
        )

        security_type_group = self._build_allocation_group(
            group_by="security_type",
            positions=fresh_usd_positions,
            key_fn=lambda pos: _derive_security_type(pos.asset_class),
            label_fn=_format_label,
            denominator=denominator,
            excluded_count=excluded_count,
        )

        return {
            "account": account_group,
            "asset_class": asset_class_group,
            "security_type": security_type_group,
        }

    def _calculate_gain_loss(
        self,
        gain_loss_positions: list[StoredPosition],
        total_positions_count: int,
    ) -> GainLossCoverage:
        if not gain_loss_positions:
            return GainLossCoverage(
                unrealized_gain_loss=None,
                cost_basis=None,
                market_value=None,
                included_count=0,
                excluded_count=total_positions_count,
            )

        gain_loss_market_value = _sum_decimals(
            p.position.market_value for p in gain_loss_positions
        )
        gain_loss_cost_basis = _sum_decimals(
            p.position.cost_basis for p in gain_loss_positions
        )

        return GainLossCoverage(
            unrealized_gain_loss=gain_loss_market_value - gain_loss_cost_basis,
            cost_basis=gain_loss_cost_basis,
            market_value=gain_loss_market_value,
            included_count=len(gain_loss_positions),
            excluded_count=total_positions_count - len(gain_loss_positions),
        )

    def _collect_warnings(
        self,
        stored_accounts: list[StoredAccount],
        latest_refresh: RefreshResult | None,
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if latest_refresh:
            for outcome in latest_refresh.provider_outcomes:
                if outcome.warning:
                    warnings.append(outcome.warning)
        if any(a.is_stale for a in stored_accounts):
            if not any("stale" in w.lower() for w in warnings):
                warnings.append("One or more accounts contain stale data.")
        return tuple(warnings)

    def _build_history(
        self, all_daily: list[DailyAccountValue], total_accounts: int
    ) -> RecordedHistory:
        date_groups: dict[date, list[DailyAccountValue]] = defaultdict(list)
        all_currencies: set[str] = set()

        for d in all_daily:
            all_currencies.add(d.currency)
            if d.currency == "USD":
                date_groups[d.snapshot_date].append(d)

        history_points: list[DailyRecordedPoint] = []
        for snap_date in sorted(date_groups.keys()):
            snaps = date_groups[snap_date]
            tot_val = _sum_decimals(s.value for s in snaps)
            history_points.append(
                DailyRecordedPoint(
                    snapshot_date=snap_date,
                    value=tot_val,
                    currency="USD",
                    accounts_count=len(snaps),
                    accounts_total=total_accounts,
                )
            )

        currencies = tuple(sorted(all_currencies)) if all_currencies else ()
        return RecordedHistory(points=tuple(history_points), currencies=currencies)
