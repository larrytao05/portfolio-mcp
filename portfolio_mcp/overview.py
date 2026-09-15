from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from portfolio_mcp.database import PortfolioRepository, StoredPosition
from portfolio_mcp.models import Position


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
            "market_value": (
                str(self.market_value) if self.market_value is not None else None
            ),
            "is_stale": self.is_stale,
            "percentage_of_total": (
                str(self.percentage_of_total)
                if self.percentage_of_total is not None
                else None
            ),
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
            "unrealized_gain_loss": (
                str(self.unrealized_gain_loss)
                if self.unrealized_gain_loss is not None
                else None
            ),
            "cost_basis": (
                str(self.cost_basis) if self.cost_basis is not None else None
            ),
            "market_value": (
                str(self.market_value) if self.market_value is not None else None
            ),
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
            "date": self.snapshot_date.isoformat(),
            "value": str(self.value),
            "currency": self.currency,
            "accounts_count": self.accounts_count,
        }


@dataclass(frozen=True)
class RecordedHistory:
    points: tuple[DailyRecordedPoint, ...]
    currencies: tuple[str, ...] = ("USD",)

    def to_dict(self) -> list[dict[str, object]]:
        return [p.to_dict() for p in self.points]


@dataclass(frozen=True)
class PortfolioOverview:
    total_known_usd_value: Decimal | None
    as_of: date | None
    refreshed_at: datetime | None
    status: str
    accounts: tuple[OverviewAccountContribution, ...]
    allocations: dict[str, AllocationGroup]
    gain_loss: GainLossCoverage
    exclusions: tuple[OverviewExclusion, ...]
    warnings: tuple[str, ...]
    history: RecordedHistory
    cash_usd: Decimal | None = None
    buying_power_usd: Decimal | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "total_known_usd_value": (
                str(self.total_known_usd_value)
                if self.total_known_usd_value is not None
                else None
            ),
            "as_of": self.as_of.isoformat() if self.as_of is not None else None,
            "refreshed_at": (
                self.refreshed_at.isoformat() if self.refreshed_at is not None else None
            ),
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
        accounts = self._repository.list_accounts()
        if not accounts:
            return PortfolioOverview(
                total_known_usd_value=None,
                as_of=None,
                refreshed_at=None,
                status="empty",
                accounts=(),
                allocations={
                    "account": AllocationGroup("account", Decimal("0"), (), 0, 0),
                    "asset_class": AllocationGroup(
                        "asset_class", Decimal("0"), (), 0, 0
                    ),
                },
                gain_loss=GainLossCoverage(None, None, None, 0, 0),
                exclusions=(),
                warnings=(),
                history=RecordedHistory(()),
            )

        latest_refresh = self._repository.latest_refresh()
        refreshed_at = (
            latest_refresh.completed_at
            if latest_refresh is not None
            else max((a.source_refreshed_at for a in accounts), default=None)
        )

        all_stored_positions: list[StoredPosition] = []
        for account in accounts:
            positions = self._repository.list_positions(account.account.id)
            if positions:
                all_stored_positions.extend(positions)

        as_of = (
            max((p.as_of for p in all_stored_positions), default=None)
            if all_stored_positions
            else None
        )

        # Status & warnings
        warnings: list[str] = []
        if latest_refresh is not None:
            warnings.extend(
                outcome.warning
                for outcome in latest_refresh.provider_outcomes
                if outcome.warning is not None
            )
            if latest_refresh.error_message:
                warnings.append(latest_refresh.error_message)

        stale_accounts = [a for a in accounts if a.is_stale]
        for sa in stale_accounts:
            msg = f"Account '{sa.account.label}' is stale"
            if msg not in warnings:
                warnings.append(msg)

        if len(stale_accounts) == len(accounts) or (
            latest_refresh is not None and latest_refresh.status == "failed"
        ):
            status = "stale"
        elif (
            len(stale_accounts) > 0
            or (latest_refresh is not None and latest_refresh.status == "partial")
            or len(warnings) > 0
        ):
            status = "partial"
        else:
            status = "fresh"

        # Exclusions and Included USD positions
        exclusions: list[OverviewExclusion] = []
        included_positions: list[Position] = []
        excluded_positions_count = 0

        for sp in all_stored_positions:
            pos = sp.position
            if pos.currency != "USD":
                exclusions.append(
                    OverviewExclusion(
                        reason="unsupported_currency",
                        symbol=pos.symbol,
                        account_id=pos.account_id,
                        details=(
                            f"Position currency '{pos.currency}' is "
                            "not supported in USD aggregates"
                        ),
                    )
                )
                excluded_positions_count += 1
            elif pos.market_value is None:
                exclusions.append(
                    OverviewExclusion(
                        reason="missing_market_value",
                        symbol=pos.symbol,
                        account_id=pos.account_id,
                        details=f"Position '{pos.symbol}' is missing market value",
                    )
                )
                excluded_positions_count += 1
            else:
                included_positions.append(pos)

        # Total known USD value
        total_usd_value = (
            sum(
                (
                    p.market_value
                    for p in included_positions
                    if p.market_value is not None
                ),
                start=Decimal("0"),
            )
            if included_positions
            else None
        )

        # Account contributions
        account_contributions: list[OverviewAccountContribution] = []
        for account in accounts:
            acc_positions = [
                p for p in included_positions if p.account_id == account.account.id
            ]
            if acc_positions:
                acc_val = sum(
                    (
                        p.market_value
                        for p in acc_positions
                        if p.market_value is not None
                    ),
                    start=Decimal("0"),
                )
            else:
                acc_val = None

            if (
                total_usd_value is not None
                and total_usd_value > 0
                and acc_val is not None
            ):
                acc_pct = (acc_val / total_usd_value).quantize(Decimal("0.0001"))
            else:
                acc_pct = None

            account_contributions.append(
                OverviewAccountContribution(
                    account_id=account.account.id,
                    label=account.account.label,
                    provider=account.account.provider,
                    account_type=account.account.account_type,
                    currency=account.account.currency,
                    market_value=acc_val,
                    is_stale=account.is_stale,
                    percentage_of_total=acc_pct,
                )
            )

        # Allocations
        alloc_denominator = total_usd_value or Decimal("0")
        included_count = len(included_positions)

        # By account
        account_slices: list[AllocationSlice] = []
        for account in accounts:
            acc_positions = [
                p for p in included_positions if p.account_id == account.account.id
            ]
            if not acc_positions:
                continue
            amt = sum(
                (p.market_value for p in acc_positions if p.market_value is not None),
                start=Decimal("0"),
            )
            pct = (
                (amt / alloc_denominator).quantize(Decimal("0.0001"))
                if alloc_denominator > 0
                else Decimal("0.0000")
            )
            account_slices.append(
                AllocationSlice(
                    key=account.account.id,
                    label=account.account.label,
                    amount=amt,
                    percentage=pct,
                    position_count=len(acc_positions),
                )
            )
        account_slices.sort(key=lambda s: s.amount, reverse=True)

        # By asset_class
        asset_class_groups: dict[str, list[Position]] = defaultdict(list)
        for p in included_positions:
            asset_class_groups[p.asset_class].append(p)

        asset_slices: list[AllocationSlice] = []
        for ac, positions in asset_class_groups.items():
            amt = sum(
                (p.market_value for p in positions if p.market_value is not None),
                start=Decimal("0"),
            )
            pct = (
                (amt / alloc_denominator).quantize(Decimal("0.0001"))
                if alloc_denominator > 0
                else Decimal("0.0000")
            )
            asset_slices.append(
                AllocationSlice(
                    key=ac,
                    label=ac,
                    amount=amt,
                    percentage=pct,
                    position_count=len(positions),
                )
            )
        asset_slices.sort(key=lambda s: s.amount, reverse=True)

        allocations = {
            "account": AllocationGroup(
                group_by="account",
                denominator=alloc_denominator,
                slices=tuple(account_slices),
                included_count=included_count,
                excluded_count=excluded_positions_count,
            ),
            "asset_class": AllocationGroup(
                group_by="asset_class",
                denominator=alloc_denominator,
                slices=tuple(asset_slices),
                included_count=included_count,
                excluded_count=excluded_positions_count,
            ),
        }

        # Gain/loss coverage
        gl_included: list[Position] = []
        for sp in all_stored_positions:
            pos = sp.position
            if (
                pos.currency == "USD"
                and pos.market_value is not None
                and pos.cost_basis is not None
            ):
                gl_included.append(pos)
            elif (
                pos.currency == "USD"
                and pos.market_value is not None
                and pos.cost_basis is None
            ):
                exclusions.append(
                    OverviewExclusion(
                        reason="missing_cost_basis",
                        symbol=pos.symbol,
                        account_id=pos.account_id,
                        details=f"Position '{pos.symbol}' is missing cost basis",
                    )
                )

        gl_excluded_count = len(all_stored_positions) - len(gl_included)
        if gl_included:
            gl_market_val = sum(
                (p.market_value for p in gl_included if p.market_value is not None),
                start=Decimal("0"),
            )
            gl_cost_basis = sum(
                (p.cost_basis for p in gl_included if p.cost_basis is not None),
                start=Decimal("0"),
            )
            gain_loss = GainLossCoverage(
                unrealized_gain_loss=gl_market_val - gl_cost_basis,
                cost_basis=gl_cost_basis,
                market_value=gl_market_val,
                included_count=len(gl_included),
                excluded_count=gl_excluded_count,
            )
        else:
            gain_loss = GainLossCoverage(
                unrealized_gain_loss=None,
                cost_basis=None,
                market_value=None,
                included_count=0,
                excluded_count=gl_excluded_count,
            )

        history = self.get_history()

        return PortfolioOverview(
            total_known_usd_value=total_usd_value,
            as_of=as_of,
            refreshed_at=refreshed_at,
            status=status,
            accounts=tuple(account_contributions),
            allocations=allocations,
            gain_loss=gain_loss,
            exclusions=tuple(exclusions),
            warnings=tuple(warnings),
            history=history,
        )

    def get_history(self) -> RecordedHistory:
        if hasattr(self._repository, "all_daily_values"):
            all_values = self._repository.all_daily_values()
        else:
            accounts = self._repository.list_accounts()
            all_values = []
            for acc in accounts:
                vals = self._repository.daily_values(acc.account.id)
                if vals:
                    all_values.extend(vals)

        by_date: dict[date, list] = defaultdict(list)
        for val in all_values:
            if val.currency == "USD":
                by_date[val.snapshot_date].append(val)

        points: list[DailyRecordedPoint] = []
        for snap_date in sorted(by_date.keys()):
            vals = by_date[snap_date]
            tot = sum((v.value for v in vals), start=Decimal("0"))
            acc_count = len({v.account_id for v in vals})
            points.append(
                DailyRecordedPoint(
                    snapshot_date=snap_date,
                    value=tot,
                    currency="USD",
                    accounts_count=acc_count,
                )
            )

        return RecordedHistory(points=tuple(points))
