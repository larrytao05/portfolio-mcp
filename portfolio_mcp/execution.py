from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class OrderState(StrEnum):
    SUBMITTING = "SUBMITTING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class CapabilityState(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class ExecutionError(ValueError):
    pass


class ExecutionIndeterminateError(ExecutionError):
    pass


_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.SUBMITTING: {
        OrderState.ACCEPTED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
    },
    OrderState.ACCEPTED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.CANCEL_PENDING,
        OrderState.UNKNOWN,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.CANCEL_PENDING,
        OrderState.UNKNOWN,
    },
    OrderState.CANCEL_PENDING: {
        OrderState.ACCEPTED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.UNKNOWN,
    },
    OrderState.REJECTED: set(),
    OrderState.FILLED: set(),
    OrderState.CANCELED: set(),
    OrderState.EXPIRED: set(),
    OrderState.UNKNOWN: {
        OrderState.ACCEPTED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.UNKNOWN,
    },
}


def require_transition(current: OrderState, next_state: OrderState) -> None:
    if next_state not in _TRANSITIONS[current]:
        raise ExecutionError(f"Cannot transition from {current} to {next_state}")


@dataclass(frozen=True)
class ExecutionCommand:
    client_order_id: str
    account_id: str
    provider: str
    instrument_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    time_in_force: str = "day"


@dataclass(frozen=True)
class ExecutionCapability:
    account_id: str
    state: CapabilityState
    can_submit: bool = False
    can_cancel: bool = False

    @property
    def permits_submission(self) -> bool:
        return self.state == CapabilityState.SUPPORTED and self.can_submit

    @property
    def permits_cancellation(self) -> bool:
        return self.state == CapabilityState.SUPPORTED and self.can_cancel


@dataclass(frozen=True)
class FillSummary:
    quantity: Decimal
    average_price: Decimal | None


@dataclass(frozen=True)
class ExecutionResult:
    state: OrderState
    broker_order_id: str | None = None
    message: str | None = None
    fill: FillSummary | None = None


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    client_order_id: str | None
    account_id: str
    instrument_id: str
    side: str
    order_type: str
    quantity: Decimal | None
    limit_price: Decimal | None
    time_in_force: str
    submitted_at: datetime
    state: OrderState
    updated_at: datetime | None = None
    status_label: str | None = None
    fill: FillSummary | None = None


@dataclass(frozen=True)
class BrokerOrderSearch:
    orders: tuple[BrokerOrderSnapshot, ...]
    complete: bool


class OrderReadProvider(Protocol):
    supports_client_order_id_lookup: bool

    async def find_by_broker_order_id(
        self, account_id: str, broker_order_id: str
    ) -> BrokerOrderSnapshot | None: ...

    async def find_by_client_order_id(
        self, account_id: str, client_order_id: str
    ) -> BrokerOrderSearch: ...

    async def search_orders(
        self, account_id: str, start_at: datetime, end_at: datetime
    ) -> BrokerOrderSearch: ...


class ExecutionProvider(Protocol):
    async def get_execution_capability(
        self, account_id: str
    ) -> ExecutionCapability: ...

    async def submit_order(self, command: ExecutionCommand) -> ExecutionResult: ...

    async def get_order(self, broker_order_id: str) -> ExecutionResult | None: ...

    async def list_recent_orders(
        self, client_order_id: str, limit: int
    ) -> list[ExecutionResult]: ...

    async def cancel_order(self, broker_order_id: str) -> ExecutionResult: ...


class FixtureExecutionProvider:
    """A deterministic, offline execution adapter used for local development."""

    def __init__(
        self,
        scenario: str = "accepted",
        cancel_scenario: str | None = None,
        capabilities: dict[str, ExecutionCapability] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.scenario = scenario
        self.cancel_scenario = cancel_scenario
        self.invocations: list[tuple[str, str]] = []
        self._orders: dict[str, ExecutionResult] = {}
        self._by_client_order_id: dict[str, list[ExecutionResult]] = {}
        self._snapshots_by_broker_id: dict[str, BrokerOrderSnapshot] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capabilities = (
            capabilities
            if capabilities is not None
            else {
                "schwab-taxable-demo": ExecutionCapability(
                    account_id="schwab-taxable-demo",
                    state=CapabilityState.SUPPORTED,
                    can_submit=True,
                    can_cancel=True,
                )
            }
        )

    async def get_execution_capability(self, account_id: str) -> ExecutionCapability:
        self.invocations.append(("capability", account_id))
        return self._capabilities.get(
            account_id,
            ExecutionCapability(
                account_id=account_id,
                state=CapabilityState.UNKNOWN,
            ),
        )

    async def submit_order(self, command: ExecutionCommand) -> ExecutionResult:
        self.invocations.append(("submit", command.client_order_id))
        if self.scenario in {"timeout", "unknown"}:
            raise ExecutionIndeterminateError("Fixture provider outcome is unknown")
        if self.scenario == "accepted_then_unknown":
            self._record_submission(command, self._result_for("accepted", command))
            raise ExecutionIndeterminateError("Fixture provider outcome is unknown")
        result = self._result_for(self.scenario, command)
        self._record_submission(command, result)
        return result

    def _record_submission(
        self, command: ExecutionCommand, result: ExecutionResult
    ) -> None:
        self._by_client_order_id.setdefault(command.client_order_id, []).append(result)
        if result.broker_order_id is None:
            return
        self._orders[result.broker_order_id] = result
        self._snapshots_by_broker_id[result.broker_order_id] = BrokerOrderSnapshot(
            broker_order_id=result.broker_order_id,
            client_order_id=command.client_order_id,
            account_id=command.account_id,
            instrument_id=command.instrument_id,
            side=command.side,
            order_type=command.order_type,
            quantity=command.quantity,
            limit_price=command.limit_price,
            time_in_force=command.time_in_force,
            submitted_at=self._clock(),
            state=result.state,
            updated_at=self._clock(),
            status_label={
                OrderState.ACCEPTED: "OPEN",
                OrderState.PARTIALLY_FILLED: "PARTIALLY_FILLED",
                OrderState.FILLED: "FILLED",
                OrderState.REJECTED: "REJECTED",
                OrderState.CANCELED: "CANCELED",
                OrderState.EXPIRED: "EXPIRED",
                OrderState.UNKNOWN: "OPEN",
            }.get(result.state),
            fill=result.fill,
        )

    async def get_order(self, broker_order_id: str) -> ExecutionResult | None:
        self.invocations.append(("lookup", broker_order_id))
        return self._orders.get(broker_order_id)

    async def list_recent_orders(
        self, client_order_id: str, limit: int
    ) -> list[ExecutionResult]:
        if not 1 <= limit <= 100:
            raise ExecutionError("Recent-order limit must be between 1 and 100")
        self.invocations.append(("recent", client_order_id))
        return self._by_client_order_id.get(client_order_id, [])[:limit]

    async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
        self.invocations.append(("cancel", broker_order_id))
        scenario = self.cancel_scenario or self.scenario
        snapshot = self._snapshots_by_broker_id.get(broker_order_id)
        fill = snapshot.fill if snapshot is not None else None
        if scenario == "cancel_unknown":
            raise ExecutionIndeterminateError("Fixture cancellation outcome is unknown")
        if scenario == "cancel_rejected":
            return ExecutionResult(
                OrderState.ACCEPTED, broker_order_id, "Cancel rejected", fill=fill
            )
        result = ExecutionResult(OrderState.CANCELED, broker_order_id, fill=fill)
        self._orders[broker_order_id] = result
        if snapshot is not None:
            self._snapshots_by_broker_id[broker_order_id] = replace(
                snapshot,
                state=OrderState.CANCELED,
                status_label="CANCELED",
                updated_at=self._clock(),
            )
        return result

    async def find_by_broker_order_id(
        self, account_id: str, broker_order_id: str
    ) -> BrokerOrderSnapshot | None:
        self.invocations.append(("read_broker_order_id", broker_order_id))
        snapshot = self._snapshots_by_broker_id.get(broker_order_id)
        return (
            snapshot
            if snapshot is not None and snapshot.account_id == account_id
            else None
        )

    async def find_by_client_order_id(
        self, account_id: str, client_order_id: str
    ) -> BrokerOrderSearch:
        self.invocations.append(("read_client_order_id", client_order_id))
        snapshots = tuple(
            snapshot
            for snapshot in self._snapshots_by_broker_id.values()
            if snapshot.account_id == account_id
            and snapshot.client_order_id == client_order_id
        )
        return BrokerOrderSearch(snapshots, complete=True)

    async def search_orders(
        self, account_id: str, start_at: datetime, end_at: datetime
    ) -> BrokerOrderSearch:
        self.invocations.append(("read_window", account_id))
        snapshots = tuple(
            snapshot
            for snapshot in self._snapshots_by_broker_id.values()
            if snapshot.account_id == account_id
            and start_at <= snapshot.submitted_at <= end_at
        )
        return BrokerOrderSearch(snapshots, complete=True)

    supports_client_order_id_lookup: bool = True

    def _result_for(self, scenario: str, command: ExecutionCommand) -> ExecutionResult:
        broker_order_id = f"fixture-{command.client_order_id}"
        if scenario == "rejected":
            return ExecutionResult(OrderState.REJECTED, None, "Fixture rejection")
        if scenario == "partial_fill":
            return ExecutionResult(
                OrderState.PARTIALLY_FILLED,
                broker_order_id,
                fill=FillSummary(command.quantity / Decimal("2"), Decimal("100")),
            )
        if scenario == "filled":
            return ExecutionResult(
                OrderState.FILLED,
                broker_order_id,
                fill=FillSummary(command.quantity, Decimal("100")),
            )
        if scenario in {"cancel_unknown", "cancel_rejected"}:
            return ExecutionResult(OrderState.ACCEPTED, broker_order_id)
        if scenario != "accepted":
            raise ExecutionError("Unsupported fixture execution scenario")
        return ExecutionResult(OrderState.ACCEPTED, broker_order_id)
