import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import (
    delete,
    func,
    select,
    update,
)
from sqlalchemy.orm import Session

from portfolio_mcp.database import Database
from portfolio_mcp.models import (
    Account,
    AccountCapabilities,
    CapabilityBlock,
    HoldingsSnapshot,
    ProviderHealth,
    ProviderHealthState,
    Transaction,
    is_schwab_account_eligible,
)
from portfolio_mcp.portfolio_records import (
    AccountDetail,
    Activity,
    DailyAccountValue,
    PortfolioView,
    ProviderRefreshOutcome,
    RefreshResult,
    StoredAccount,
    StoredPosition,
    _activity,
    _refresh_result,
    _stored_account,
    _stored_position,
)
from portfolio_mcp.schema import (
    AccountCapabilityRecord,
    AccountRecord,
    ActivityRecord,
    DailyAccountValueRecord,
    PositionRecord,
    RefreshProviderOutcomeRecord,
    RefreshRunRecord,
)


def stored_account(db: Database, account_id: str) -> StoredAccount | None:
    with db.sessions() as session:
        record = session.get(AccountRecord, account_id)
        if record is None:
            return None
        return _stored_account(record)


def account_exists(db: Database, account_id: str) -> bool:
    with db.sessions() as session:
        return (
            session.scalar(
                select(AccountRecord.id).where(AccountRecord.id == account_id)
            )
            is not None
        )


def list_accounts(db: Database) -> list[StoredAccount]:
    with db.sessions() as session:
        return _list_accounts(session)


def _list_accounts(session: Session) -> list[StoredAccount]:
    records = session.scalars(select(AccountRecord).order_by(AccountRecord.label))
    return [_stored_account(record) for record in records]


def list_positions(db: Database, account_id: str) -> list[StoredPosition] | None:
    with db.sessions() as session:
        account = session.get(AccountRecord, account_id)
        if account is None:
            return None
        records = session.scalars(
            select(PositionRecord)
            .where(PositionRecord.account_id == account_id)
            .order_by(PositionRecord.symbol)
        )
        return [_stored_position(record, account) for record in records]


def account_detail(db: Database, account_id: str) -> AccountDetail | None:
    with db.sessions() as session:
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
        positions = tuple(_stored_position(record, account) for record in records)
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


def account_capability(db: Database, account_id: str) -> AccountCapabilities | None:
    with db.sessions() as session:
        account = session.get(AccountRecord, account_id)
        if account is None:
            return None
        record = session.get(AccountCapabilityRecord, account_id)
        if record is not None and not record.is_current:
            return None
        return (
            _capability(db, record, account)
            if record is not None
            else AccountCapabilities.unknown(_stored_account(account).account)
        )


def current_capabilities(db: Database) -> list[AccountCapabilities]:
    with db.sessions() as session:
        records = session.execute(
            select(AccountRecord, AccountCapabilityRecord)
            .outerjoin(
                AccountCapabilityRecord,
                AccountCapabilityRecord.account_id == AccountRecord.id,
            )
            .where(
                AccountCapabilityRecord.is_current.is_(True)
                | AccountCapabilityRecord.account_id.is_(None)
            )
            .order_by(AccountRecord.label)
        ).all()
        return [
            _capability(db, capability, account)
            if capability is not None
            else AccountCapabilities.unknown(_stored_account(account).account)
            for account, capability in records
        ]


def provider_health(db: Database) -> list[ProviderHealth]:
    with db.sessions() as session:
        session.connection().exec_driver_sql("BEGIN")
        accounts = list(
            session.scalars(select(AccountRecord).order_by(AccountRecord.provider))
        )
        accounts_by_provider: dict[str, list[AccountRecord]] = {}
        for account in accounts:
            accounts_by_provider.setdefault(account.provider, []).append(account)
        latest = session.scalar(
            select(RefreshRunRecord).order_by(RefreshRunRecord.id.desc()).limit(1)
        )
        outcomes = (
            {item.provider: item for item in _outcomes_for_refresh(session, latest.id)}
            if latest is not None
            else {}
        )
        refresh_status = latest.status if latest is not None else None
        error_code = latest.error_code if latest is not None else None
        observed_at = latest.completed_at if latest is not None else None
        health = []
        for provider in sorted(set(accounts_by_provider) | set(outcomes)):
            state = _provider_health_state(
                outcome=outcomes.get(provider),
                accounts=accounts_by_provider.get(provider, []),
                refresh_status=refresh_status,
                error_code=error_code,
            )
            health.append(
                ProviderHealth(
                    provider=provider,
                    state=state,
                    observed_at=observed_at,
                    last_success_at=_latest_provider_success(session, provider),
                    blocks=_health_blocks(state),
                )
            )
        return health


def save_refresh(
    db: Database,
    snapshots: list[HoldingsSnapshot],
    started_at: datetime,
    completed_at: datetime,
    snapshot_date: date,
    failed_accounts: list[Account] | None = None,
    transactions: list[Transaction] | None = None,
    capabilities: list[AccountCapabilities] | None = None,
    failed_capability_accounts: list[Account] | None = None,
    current_accounts: list[Account] | None = None,
) -> "RefreshResult":
    snapshots = list({snapshot.account.id: snapshot for snapshot in snapshots}.values())
    refreshed_ids = {snapshot.account.id for snapshot in snapshots}
    failed_accounts = list(
        {
            account.id: account
            for account in failed_accounts or []
            if account.id not in refreshed_ids
        }.values()
    )
    if current_accounts is None:
        current_accounts = [
            snapshot.account for snapshot in snapshots
        ] + failed_accounts
    with db.sessions.begin() as session:
        _mark_removed_accounts(session, current_accounts)
        for snapshot in snapshots:
            _save_snapshot(session, snapshot, completed_at, snapshot_date)
        _save_transactions(session, transactions or [], completed_at)
        _save_capabilities(session, capabilities or [])
        _mark_capabilities_stale(session, failed_capability_accounts or [])

        _mark_failed_accounts(session, failed_accounts)
        session.flush()
        stored_accounts = list(session.scalars(select(AccountRecord)))
        outcomes = _provider_outcomes(snapshots, failed_accounts, stored_accounts)
        status = _refresh_status(outcomes)

        run = RefreshRunRecord(
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            account_count=len(snapshots),
            position_count=sum(len(snapshot.positions) for snapshot in snapshots),
            snapshot_count=sum(
                any(
                    position.market_value is not None for position in snapshot.positions
                )
                for snapshot in snapshots
            ),
        )
        session.add(run)
        session.flush()
        _save_outcomes(session, run.id, outcomes)
        return _refresh_result(run, outcomes)


def save_failed_refresh(
    db: Database,
    started_at: datetime,
    completed_at: datetime,
    error_code: str,
    error_message: str,
) -> RefreshResult:
    with db.sessions.begin() as session:
        stale_accounts = _mark_all_accounts_stale(session)
        _mark_all_capabilities_stale(session)
        outcomes = _failed_outcomes(stale_accounts)
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
        _save_outcomes(session, run.id, outcomes)
        return _refresh_result(run, outcomes)


def daily_values(db: Database, account_id: str) -> list[DailyAccountValue] | None:
    with db.sessions() as session:
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


def all_positions(db: Database) -> list[StoredPosition]:
    with db.sessions() as session:
        return _all_positions(session)


def _all_positions(session: Session) -> list[StoredPosition]:
    records = session.execute(
        select(PositionRecord, AccountRecord)
        .join(AccountRecord, PositionRecord.account_id == AccountRecord.id)
        .order_by(PositionRecord.account_id, PositionRecord.symbol)
    ).all()
    return [
        _stored_position(pos_record, acc_record) for pos_record, acc_record in records
    ]


def all_daily_values(db: Database) -> list[DailyAccountValue]:
    with db.sessions() as session:
        return _all_daily_values(session)


def _all_daily_values(session: Session) -> list[DailyAccountValue]:
    records = session.scalars(
        select(DailyAccountValueRecord).order_by(
            DailyAccountValueRecord.snapshot_date,
            DailyAccountValueRecord.account_id,
        )
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


def latest_refresh(db: Database) -> RefreshResult | None:
    with db.sessions() as session:
        return _latest_refresh(session)


def _latest_refresh(session: Session) -> RefreshResult | None:
    record = session.scalar(
        select(RefreshRunRecord).order_by(RefreshRunRecord.id.desc()).limit(1)
    )
    if record is None:
        return None
    return _refresh_result(record, _outcomes_for_refresh(session, record.id))


def list_activities(
    db: Database,
    *,
    account_id: str | None = None,
    provider: str | None = None,
    transaction_type: str | None = None,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list["Activity"], int]:
    with db.sessions() as session:
        query = select(ActivityRecord, AccountRecord).join(
            AccountRecord, ActivityRecord.account_id == AccountRecord.id
        )
        if account_id is not None:
            query = query.where(ActivityRecord.account_id == account_id)
        if provider is not None:
            query = query.where(AccountRecord.provider == provider)
        if transaction_type is not None:
            query = query.where(ActivityRecord.transaction_type == transaction_type)
        if symbol is not None:
            query = query.where(ActivityRecord.symbol == symbol)
        if start_date is not None:
            query = query.where(ActivityRecord.occurred_on >= start_date)
        if end_date is not None:
            query = query.where(ActivityRecord.occurred_on <= end_date)

        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        records = session.execute(
            query.order_by(
                ActivityRecord.occurred_on.desc(),
                ActivityRecord.occurred_at.desc(),
                ActivityRecord.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [_activity(activity, account) for activity, account in records], total


def _capability(
    db: Database, record: AccountCapabilityRecord, account: AccountRecord
) -> AccountCapabilities:
    return AccountCapabilities(
        account_id=record.account_id,
        provider=record.provider,
        asset_classes=tuple(json.loads(record.asset_classes)),
        supported_sides=tuple(json.loads(record.supported_sides)),
        order_types=tuple(json.loads(record.order_types)),
        time_in_force=tuple(json.loads(record.time_in_force)),
        sizing_modes=tuple(json.loads(record.sizing_modes)),
        preview_supported=record.preview_supported,
        cancellation_supported=record.cancellation_supported,
        observed_at=record.observed_at,
        last_success_at=record.last_success_at,
        source=record.source,
        blocks=tuple(CapabilityBlock(**block) for block in json.loads(record.blocks)),
        is_stale=(
            record.is_stale
            or account.is_stale
            or record.observed_at is None
            or record.observed_at < db.now() - timedelta(days=1)
        ),
        schwab_mapping_eligible=is_schwab_account_eligible(
            account.provider, account.currency
        ),
    )


def _save_capabilities(
    session: Session, capabilities: list[AccountCapabilities]
) -> None:
    for capability in capabilities:
        if session.get(AccountRecord, capability.account_id) is None:
            continue
        record = session.get(AccountCapabilityRecord, capability.account_id)
        if record is None:
            record = AccountCapabilityRecord(account_id=capability.account_id)
            session.add(record)
        record.provider = capability.provider
        record.asset_classes = json.dumps(capability.asset_classes)
        record.supported_sides = json.dumps(capability.supported_sides)
        record.order_types = json.dumps(capability.order_types)
        record.time_in_force = json.dumps(capability.time_in_force)
        record.sizing_modes = json.dumps(capability.sizing_modes)
        record.preview_supported = capability.preview_supported
        record.cancellation_supported = capability.cancellation_supported
        record.observed_at = capability.observed_at
        record.last_success_at = capability.last_success_at
        record.source = capability.source
        record.blocks = json.dumps([block.to_dict() for block in capability.blocks])
        record.is_stale = capability.is_stale
        record.is_current = True


def _mark_removed_accounts(session: Session, current_accounts: list[Account]) -> None:
    current_ids = {account.id for account in current_accounts}
    session.execute(
        update(AccountRecord)
        .where(AccountRecord.id.not_in(current_ids))
        .values(is_stale=True)
    )
    for record in session.scalars(select(AccountCapabilityRecord)):
        record.is_current = record.account_id in current_ids
        if not record.is_current:
            record.is_stale = True


def _mark_capabilities_stale(session: Session, accounts: list[Account]) -> None:
    for account in accounts:
        record = session.get(AccountCapabilityRecord, account.id)
        if record is not None:
            record.is_stale = True


def _mark_all_capabilities_stale(session: Session) -> None:
    for record in session.scalars(select(AccountCapabilityRecord)):
        record.is_stale = True


def _provider_health_state(
    *,
    outcome: ProviderRefreshOutcome | None,
    accounts: list[AccountRecord],
    refresh_status: str | None,
    error_code: str | None,
) -> ProviderHealthState:
    if outcome is None:
        return "unknown"
    if refresh_status == "failed":
        if error_code == "authentication_required":
            return "authentication_required"
        if error_code == "authorization_required":
            return "authorization_required"
        return "unavailable"
    if outcome.status == "success" and not any(
        account.is_stale for account in accounts
    ):
        return "healthy"
    return "degraded"


def _health_blocks(state: ProviderHealthState) -> tuple[CapabilityBlock, ...]:
    if state == "authentication_required":
        return (
            CapabilityBlock(
                "authentication_required",
                "Provider authentication is required.",
                "reconnect_provider",
            ),
        )
    if state == "authorization_required":
        return (
            CapabilityBlock(
                "authorization_required",
                "Provider authorization is required.",
                "reconnect_provider",
            ),
        )
    if state == "unavailable":
        return (
            CapabilityBlock(
                "provider_unavailable",
                "Provider status is unavailable. Refresh again later.",
            ),
        )
    return ()


def _latest_provider_success(session: Session, provider: str) -> datetime | None:
    outcome = session.scalar(
        select(RefreshProviderOutcomeRecord)
        .where(
            RefreshProviderOutcomeRecord.provider == provider,
            RefreshProviderOutcomeRecord.status == "success",
        )
        .order_by(RefreshProviderOutcomeRecord.id.desc())
        .limit(1)
    )
    if outcome is None:
        return None
    run = session.get(RefreshRunRecord, outcome.refresh_id)
    return run.completed_at if run is not None else None


def _save_snapshot(
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
        delete(PositionRecord).where(PositionRecord.account_id == snapshot.account.id)
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
        and position.currency == snapshot.account.currency
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


def _mark_failed_accounts(session: Session, failed_accounts: list[Account]) -> set[str]:
    stale_account_ids: set[str] = set()
    for account in failed_accounts:
        record = session.get(AccountRecord, account.id)
        if record is None:
            continue
        record.is_stale = True
        stale_account_ids.add(account.id)
    return stale_account_ids


def _refresh_status(outcomes: tuple[ProviderRefreshOutcome, ...]) -> str:
    if all(outcome.status == "success" for outcome in outcomes):
        return "success"
    if any(outcome.accounts_refreshed for outcome in outcomes):
        return "partial"
    return "failed"


def _mark_all_accounts_stale(session: Session) -> list[AccountRecord]:
    records = list(session.scalars(select(AccountRecord)))
    for record in records:
        record.is_stale = True
    return records


def _provider_outcomes(
    snapshots: list[HoldingsSnapshot],
    failed_accounts: list[Account],
    stored_accounts: list[AccountRecord],
) -> tuple[ProviderRefreshOutcome, ...]:
    counts: dict[str, dict[str, int]] = {}
    for snapshot in snapshots:
        provider = counts.setdefault(
            snapshot.account.provider, {"refreshed": 0, "stale": 0, "excluded": 0}
        )
        provider["refreshed"] += 1
    for account in stored_accounts:
        if account.is_stale:
            provider = counts.setdefault(
                account.provider, {"refreshed": 0, "stale": 0, "excluded": 0}
            )
            provider["stale"] += 1
    stored_ids = {account.id for account in stored_accounts}
    for account in failed_accounts:
        if account.id not in stored_ids:
            provider = counts.setdefault(
                account.provider, {"refreshed": 0, "stale": 0, "excluded": 0}
            )
            provider["excluded"] += 1

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
                warning=_outcome_warning(provider, count["stale"], count["excluded"]),
            )
        )
    return tuple(outcomes)


def _failed_outcomes(
    stale_accounts: list[AccountRecord],
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
    provider: str, stale_accounts: int, excluded_accounts: int
) -> str | None:
    if stale_accounts and excluded_accounts:
        return (
            f"{provider} has stale data and unavailable accounts excluded from totals."
        )
    if stale_accounts:
        return f"{provider} data is stale; last successful data is shown."
    if excluded_accounts:
        return f"{provider} data is unavailable and excluded from totals."
    return None


def _save_outcomes(
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
    session: Session, refresh_id: int
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


def _save_transactions(
    session: Session, transactions: list[Transaction], imported_at: datetime
) -> None:
    for transaction in transactions:
        record = session.scalar(
            select(ActivityRecord).where(
                ActivityRecord.account_id == transaction.account_id,
                ActivityRecord.provider_transaction_id == transaction.id,
            )
        )
        if record is None:
            record = ActivityRecord(
                account_id=transaction.account_id,
                provider_transaction_id=transaction.id,
                imported_at=imported_at,
            )
            session.add(record)
        record.occurred_on = transaction.occurred_on
        record.occurred_at = transaction.occurred_at
        record.transaction_type = transaction.transaction_type
        record.symbol = transaction.symbol
        record.description = transaction.description
        record.quantity = transaction.quantity
        record.amount = transaction.amount
        record.fees = transaction.fees
        record.currency = transaction.currency


def read_portfolio_view(db: Database) -> PortfolioView:
    with db.sessions() as session:
        session.connection().exec_driver_sql("BEGIN")
        return PortfolioView(
            latest_refresh=_latest_refresh(session),
            accounts=tuple(_list_accounts(session)),
            positions=tuple(_all_positions(session)),
            daily_values=tuple(_all_daily_values(session)),
        )
