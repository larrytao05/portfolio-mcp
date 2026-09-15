from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from portfolio_mcp.database import PortfolioRepository


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

    def to_dict(self) -> dict[str, object]:
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

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_date": self.snapshot_date.isoformat(),
            "value": str(self.value),
            "currency": self.currency,
            "accounts_count": self.accounts_count,
        }


@dataclass(frozen=True)
class RecordedHistory:
    points: tuple[DailyRecordedPoint, ...]
    currencies: tuple[str, ...]

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


def _sum_decimals(values: Iterable[Decimal | None]) -> Decimal:
    total = Decimal("0")
    for v in values:
        if v is not None:
            total += v
    return total


def _format_asset_class_label(asset_class: str) -> str:
    labels = {
        "equity": "Equity",
        "equity_etf": "Equity ETF",
        "bond_etf": "Bond ETF",
        "commodity_etf": "Commodity ETF",
        "cash": "Cash",
        "crypto": "Cryptocurrency",
        "mutual_fund": "Mutual Fund",
    }
    return labels.get(asset_class, asset_class.replace("_", " ").title())


class OverviewService:
    def __init__(self, repository: PortfolioRepository) -> None:
        self._repository = repository

    def get_overview(self) -> PortfolioOverview:
        stored_accounts = self._repository.list_accounts()
        if not stored_accounts:
            return PortfolioOverview(
                total_known_usd_value=None,
                cash_usd=None,
                buying_power_usd=None,
                as_of=None,
                refreshed_at=None,
                status="empty",
                accounts=(),
                allocations={
                    "account": AllocationGroup(
                        group_by="account",
                        denominator=Decimal("0"),
                        slices=(),
                        included_count=0,
                        excluded_count=0,
                    ),
                    "asset_class": AllocationGroup(
                        group_by="asset_class",
                        denominator=Decimal("0"),
                        slices=(),
                        included_count=0,
                        excluded_count=0,
                    ),
                },
                gain_loss=GainLossCoverage(
                    unrealized_gain_loss=None,
                    cost_basis=None,
                    market_value=None,
                    included_count=0,
                    excluded_count=0,
                ),
                exclusions=(),
                warnings=(),
                history=RecordedHistory(points=(), currencies=()),
            )

        stored_positions = self._repository.all_positions()
        latest_refresh = self._repository.latest_refresh()
        all_daily = self._repository.all_daily_values()

        # Determine status
        if all(a.is_stale for a in stored_accounts) or (
            latest_refresh and latest_refresh.status == "failed"
        ):
            status = "stale"
        elif any(a.is_stale for a in stored_accounts) or (
            latest_refresh and latest_refresh.status == "partial"
        ):
            status = "partial"
        else:
            status = "fresh"

        # Determine refreshed_at and as_of
        refreshed_at = (
            latest_refresh.completed_at
            if latest_refresh
            else max(a.source_refreshed_at for a in stored_accounts)
        )
        as_of = max((p.as_of for p in stored_positions), default=None)

        # Classify positions
        exclusions: list[OverviewExclusion] = []
        included_positions = []
        gain_loss_positions = []

        total_positions_count = len(stored_positions)

        for stored_pos in stored_positions:
            pos = stored_pos.position
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

            # Position is valid USD holding with known market value
            included_positions.append(stored_pos)

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

        # Aggregate total USD value
        total_known_usd = _sum_decimals(
            p.position.market_value for p in included_positions
        )

        # Cash USD
        cash_positions = [
            p
            for p in included_positions
            if p.position.asset_class.lower() in ("cash", "cash_equivalent")
            or p.position.symbol.upper() in ("CASH", "USD")
        ]
        cash_usd = (
            sum((p.position.market_value for p in cash_positions), start=Decimal("0"))
            if cash_positions
            else None
        )

        # Per-account contribution
        account_contributions = []
        account_slices = []
        total_excluded_for_allocations = total_positions_count - len(included_positions)

        for stored_acc in stored_accounts:
            acc = stored_acc.account
            acc_positions = [
                p for p in included_positions if p.position.account_id == acc.id
            ]
            if acc_positions or any(
                p.position.account_id == acc.id for p in stored_positions
            ):
                acc_val = sum(
                    (p.position.market_value for p in acc_positions), start=Decimal("0")
                )
            else:
                acc_val = None

            if acc_val is not None and total_known_usd > 0:
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

            if acc_val is not None and acc_val > 0:
                account_slices.append(
                    AllocationSlice(
                        key=acc.id,
                        label=acc.label,
                        amount=acc_val,
                        percentage=pct if pct is not None else Decimal("0.0000"),
                        position_count=len(acc_positions),
                    )
                )

        account_slices.sort(key=lambda s: (-s.amount, s.label))

        # Asset class allocation
        asset_class_groups: dict[str, list] = {}
        for p in included_positions:
            ac = p.position.asset_class
            asset_class_groups.setdefault(ac, []).append(p)

        asset_class_slices = []
        for ac, positions in asset_class_groups.items():
            amount = sum(
                (p.position.market_value for p in positions), start=Decimal("0")
            )
            pct = (
                (amount / total_known_usd).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
                if total_known_usd > 0
                else Decimal("0.0000")
            )
            asset_class_slices.append(
                AllocationSlice(
                    key=ac,
                    label=_format_asset_class_label(ac),
                    amount=amount,
                    percentage=pct,
                    position_count=len(positions),
                )
            )
        asset_class_slices.sort(key=lambda s: (-s.amount, s.label))

        allocations = {
            "account": AllocationGroup(
                group_by="account",
                denominator=total_known_usd,
                slices=tuple(account_slices),
                included_count=len(included_positions),
                excluded_count=total_excluded_for_allocations,
            ),
            "asset_class": AllocationGroup(
                group_by="asset_class",
                denominator=total_known_usd,
                slices=tuple(asset_class_slices),
                included_count=len(included_positions),
                excluded_count=total_excluded_for_allocations,
            ),
        }

        # Gain/Loss coverage
        if gain_loss_positions:
            gl_mv = sum(
                (p.position.market_value for p in gain_loss_positions),
                start=Decimal("0"),
            )
            gl_cb = sum(
                (p.position.cost_basis for p in gain_loss_positions), start=Decimal("0")
            )
            gain_loss = GainLossCoverage(
                unrealized_gain_loss=gl_mv - gl_cb,
                cost_basis=gl_cb,
                market_value=gl_mv,
                included_count=len(gain_loss_positions),
                excluded_count=total_positions_count - len(gain_loss_positions),
            )
        else:
            gain_loss = GainLossCoverage(
                unrealized_gain_loss=None,
                cost_basis=None,
                market_value=None,
                included_count=0,
                excluded_count=total_positions_count,
            )

        # Warnings
        warnings_list: list[str] = []
        if latest_refresh:
            for outcome in latest_refresh.provider_outcomes:
                if outcome.warning:
                    warnings_list.append(outcome.warning)
        if any(a.is_stale for a in stored_accounts):
            if not any("stale" in w.lower() for w in warnings_list):
                warnings_list.append("One or more accounts contain stale data.")

        # History
        # Group by snapshot_date for USD daily snapshots
        date_groups: dict[date, list] = {}
        all_currencies = set()
        for d in all_daily:
            all_currencies.add(d.currency)
            if d.currency == "USD":
                date_groups.setdefault(d.snapshot_date, []).append(d)

        history_points = []
        for snap_date in sorted(date_groups.keys()):
            snaps = date_groups[snap_date]
            tot_val = _sum_decimals(s.value for s in snaps)
            history_points.append(
                DailyRecordedPoint(
                    snapshot_date=snap_date,
                    value=tot_val,
                    currency="USD",
                    accounts_count=len(snaps),
                )
            )

        currencies = tuple(sorted(all_currencies)) if all_currencies else ()
        history = RecordedHistory(points=tuple(history_points), currencies=currencies)

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
            warnings=tuple(warnings_list),
            history=history,
        )
