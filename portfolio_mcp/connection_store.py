from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from portfolio_mcp.database import Database
from portfolio_mcp.execution import ExecutionPreflightError, OrderState
from portfolio_mcp.schema import (
    AccountRouteRecord,
    CancellationAttemptRecord,
    ConnectionRecord,
    ImportedCancellationEventRecord,
    OrderRecord,
)


@dataclass(frozen=True)
class AccountRoute:
    account_id: str
    connection_id: str
    provider_route: str
    upstream_account_id: str
    credential_reference: str
    active: bool
    included: bool
    trade_enabled: bool
    version: int


class ConnectionBusyError(ValueError):
    pass


class ConnectionStore:
    def __init__(self, database: Database) -> None:
        self._sessions = database.sessions
        self._locks = database.submission_locks

    @contextmanager
    def _claim_connection(self, connection_id: str) -> Iterator[None]:
        with self._locks.claim(f"connection:{connection_id}") as acquired:
            if not acquired:
                raise ConnectionBusyError("An account action is in progress; try again")
            yield

    def dispatch_cancellation(
        self, route: AccountRoute, dispatch: Callable[[], tuple[int, object]]
    ) -> tuple[int, object]:
        try:
            with self._claim_connection(route.connection_id):
                current = self.account_route(route.account_id)
                if (
                    current is None
                    or current != route
                    or not current.active
                    or not current.included
                    or not current.trade_enabled
                    or current.provider_route != "alpaca"
                    or current.credential_reference != "server_env:alpaca"
                ):
                    raise ExecutionPreflightError(
                        "Account route changed before cancellation"
                    )
                return dispatch()
        except ConnectionBusyError:
            raise ExecutionPreflightError("Account action is in progress") from None

    def reserve_cancellation(
        self, account_id: str, broker_order_id: str, now: datetime
    ) -> tuple[str, bool]:
        try:
            with self._sessions.begin() as session:
                locked = session.execute(
                    update(AccountRouteRecord)
                    .where(AccountRouteRecord.account_id == account_id)
                    .values(version=AccountRouteRecord.version)
                )
                if not cast(CursorResult[object], locked).rowcount:
                    raise ValueError("Account route unavailable")
                existing_order = session.scalar(
                    select(OrderRecord.id)
                    .where(
                        OrderRecord.account_id == account_id,
                        OrderRecord.state.in_(
                            (
                                OrderState.SUBMITTING,
                                OrderState.UNKNOWN,
                                OrderState.CANCEL_PENDING,
                            )
                        ),
                    )
                    .limit(1)
                )
                if existing_order is not None:
                    raise ValueError("A submission requires reconciliation")
                record = CancellationAttemptRecord(
                    account_id=account_id,
                    broker_order_id=broker_order_id,
                    state="UNKNOWN",
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.flush()
                session.add(
                    ImportedCancellationEventRecord(
                        account_id=account_id,
                        broker_order_id=broker_order_id,
                        kind="reserved",
                        state="UNKNOWN",
                        occurred_at=now,
                    )
                )
                return record.state, True
        except IntegrityError:
            state = self.cancellation_state(account_id, broker_order_id)
            if state is None:
                raise
            return state, False

    def cancellation_state(self, account_id: str, broker_order_id: str) -> str | None:
        with self._sessions() as session:
            record = session.get(
                CancellationAttemptRecord, (account_id, broker_order_id)
            )
            return record.state if record else None

    def finish_cancellation(
        self,
        account_id: str,
        broker_order_id: str,
        state: str,
        now: datetime,
        *,
        kind: str = "result",
    ) -> None:
        if kind not in {"result", "reconciliation"}:
            raise ValueError("Invalid cancellation event kind")
        if state not in {"UNKNOWN", "CANCEL_PENDING", "CANCELED", "FILLED", "REJECTED"}:
            raise ValueError("Invalid cancellation state")
        with self._sessions.begin() as session:
            result = session.execute(
                update(CancellationAttemptRecord)
                .where(
                    CancellationAttemptRecord.account_id == account_id,
                    CancellationAttemptRecord.broker_order_id == broker_order_id,
                    CancellationAttemptRecord.state.in_(("UNKNOWN", "CANCEL_PENDING")),
                )
                .values(state=state, updated_at=now)
            )
            if not cast(CursorResult[object], result).rowcount:
                raise ValueError("Cancellation is not unresolved")
            session.add(
                ImportedCancellationEventRecord(
                    account_id=account_id,
                    broker_order_id=broker_order_id,
                    kind=kind,
                    state=state,
                    occurred_at=now,
                )
            )

    def unresolved_cancellations(self, account_id: str) -> list[dict[str, str]]:
        with self._sessions() as session:
            records = session.scalars(
                select(CancellationAttemptRecord).where(
                    CancellationAttemptRecord.account_id == account_id,
                    CancellationAttemptRecord.state.in_(("UNKNOWN", "CANCEL_PENDING")),
                )
            )
            return [
                {"broker_order_id": record.broker_order_id, "state": record.state}
                for record in records
            ]

    def cancellation_attempts(self, account_id: str) -> list[dict[str, str]]:
        with self._sessions() as session:
            records = session.scalars(
                select(CancellationAttemptRecord)
                .where(CancellationAttemptRecord.account_id == account_id)
                .order_by(CancellationAttemptRecord.created_at.desc())
            )
            return [
                {
                    "broker_order_id": record.broker_order_id,
                    "state": record.state,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                }
                for record in records
            ]

    def cancellation_events(self, account_id: str) -> list[dict[str, str]]:
        with self._sessions() as session:
            records = session.scalars(
                select(ImportedCancellationEventRecord)
                .where(ImportedCancellationEventRecord.account_id == account_id)
                .order_by(ImportedCancellationEventRecord.id)
            )
            return [
                {
                    "broker_order_id": record.broker_order_id,
                    "kind": record.kind,
                    "state": record.state,
                    "occurred_at": record.occurred_at.isoformat(),
                }
                for record in records
            ]

    def has_unresolved_cancellations(self, account_id: str) -> bool:
        with self._sessions() as session:
            return (
                session.scalar(
                    select(CancellationAttemptRecord.account_id)
                    .where(
                        CancellationAttemptRecord.account_id == account_id,
                        CancellationAttemptRecord.state.in_(
                            ("UNKNOWN", "CANCEL_PENDING")
                        ),
                    )
                    .limit(1)
                )
                is not None
            )

    def save_connection(
        self,
        connection_id: str,
        provider_route: str,
        environment: str,
        credential_reference: str,
        active: bool,
    ) -> None:
        if provider_route not in {"alpaca", "snaptrade", "schwab", "fixture"}:
            raise ValueError("Unknown provider route")
        if environment not in {"live", "fixture"}:
            raise ValueError("Unknown connection environment")
        with self._claim_connection(connection_id):
            with self._sessions.begin() as session:
                record = session.get(ConnectionRecord, connection_id)
                if record is None:
                    record = ConnectionRecord(id=connection_id)
                    session.add(record)
                elif (
                    record.provider_route,
                    record.environment,
                    record.credential_reference,
                ) != (provider_route, environment, credential_reference):
                    raise ValueError("Connection identity cannot be reassigned")
                record.provider_route = provider_route
                record.environment = environment
                record.credential_reference = credential_reference
                record.active = active

    def save_account_route(
        self,
        account_id: str,
        connection_id: str,
        upstream_account_id: str,
        *,
        included: bool = True,
        trade_enabled: bool = False,
    ) -> None:
        with self._claim_connection(connection_id):
            with self._sessions.begin() as session:
                if session.get(ConnectionRecord, connection_id) is None:
                    raise ValueError("Connection not found")
                record = session.get(AccountRouteRecord, account_id)
                if record is None:
                    record = AccountRouteRecord(account_id=account_id)
                    session.add(record)
                elif (record.connection_id, record.upstream_account_id) != (
                    connection_id,
                    upstream_account_id,
                ):
                    raise ValueError("Account route cannot be reassigned")
                record.connection_id = connection_id
                record.upstream_account_id = upstream_account_id
                record.included = included
                record.trade_enabled = trade_enabled if included else False

    def change_account_settings(
        self,
        account_id: str,
        version: int,
        *,
        included: bool | None = None,
        trade_enabled: bool | None = None,
    ) -> bool:
        route = self.account_route(account_id)
        if route is None:
            return False
        connection_id = route.connection_id
        with self._claim_connection(connection_id):
            with self._sessions.begin() as session:
                active_connection = select(ConnectionRecord.id).where(
                    ConnectionRecord.active.is_(True)
                )
                query = update(AccountRouteRecord).where(
                    AccountRouteRecord.account_id == account_id,
                    AccountRouteRecord.version == version,
                    AccountRouteRecord.connection_id.in_(active_connection),
                )
                if included is not None:
                    values = {
                        "included": included,
                        "version": AccountRouteRecord.version + 1,
                    }
                    if not included:
                        values["trade_enabled"] = False
                else:
                    query = query.where(AccountRouteRecord.included.is_(True))
                    values = {
                        "trade_enabled": bool(trade_enabled),
                        "version": AccountRouteRecord.version + 1,
                    }
                result = cast(
                    CursorResult[object], session.execute(query.values(**values))
                )
                return result.rowcount == 1

    def disconnect_connection(self, connection_id: str) -> bool:
        with self._claim_connection(connection_id):
            with self._sessions.begin() as session:
                connection = session.get(ConnectionRecord, connection_id)
                if connection is None:
                    return False
                connection.active = False
                session.execute(
                    update(AccountRouteRecord)
                    .where(AccountRouteRecord.connection_id == connection_id)
                    .values(trade_enabled=False, version=AccountRouteRecord.version + 1)
                )
                return True

    def account_routes(self) -> list[AccountRoute]:
        with self._sessions() as session:
            ids = list(session.scalars(select(AccountRouteRecord.account_id)))
        return [
            route for account_id in ids if (route := self.account_route(account_id))
        ]

    def account_route(self, account_id: str) -> AccountRoute | None:
        with self._sessions() as session:
            route = session.get(AccountRouteRecord, account_id)
            if route is None:
                return None
            connection = session.get(ConnectionRecord, route.connection_id)
            if connection is None:
                return None
            return AccountRoute(
                account_id=route.account_id,
                connection_id=connection.id,
                provider_route=connection.provider_route,
                upstream_account_id=route.upstream_account_id,
                credential_reference=connection.credential_reference,
                active=connection.active,
                included=route.included,
                trade_enabled=route.trade_enabled,
                version=route.version,
            )
