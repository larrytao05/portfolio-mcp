from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from portfolio_mcp.database import (
    OrderPage,
    OrderRefreshPlan,
    PortfolioRepository,
    ReconciliationClaim,
    StoredOrder,
)
from portfolio_mcp.execution import (
    BrokerOrderSearch,
    BrokerOrderSnapshot,
    ExecutionError,
    OrderReadProvider,
    OrderState,
)
from portfolio_mcp.order_history import (
    OrderStatusSource,
    encode_order_cursor,
    require_aware_utc,
)

_MATCH_BEFORE = timedelta(seconds=30)
_MATCH_AFTER = timedelta(minutes=2)
_ALLOWED_PROVIDER_STATUS_LABELS = {
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "REJECTED",
    "CANCELED",
    "EXPIRED",
}
_SYNCABLE_STATES = {
    OrderState.ACCEPTED,
    OrderState.PARTIALLY_FILLED,
    OrderState.CANCEL_PENDING,
    OrderState.UNKNOWN,
}
_PROVIDER_READ_TIMEOUT = timedelta(seconds=10)
_SAFE_MESSAGES = {
    "matched": "Broker order status was synchronized.",
    "not_found": "No matching broker order was found.",
    "ambiguous": "Multiple broker orders matched; manual review is required.",
    "incomplete": "Broker order search was incomplete; manual review is required.",
    "mismatch": "Broker order identity did not match; manual review is required.",
    "stale": "Broker order data was older than the last observation.",
    "provider_error": "Broker order lookup failed; the saved status was preserved.",
    "refused": (
        "Broker order status could not be applied; the saved status was preserved."
    ),
}


@dataclass(frozen=True)
class OrderRefreshResult:
    order: StoredOrder
    status: str
    provider_read_started: bool
    next_refresh_at: datetime | None
    target_order_id: str | None
    server_time: datetime


class OrderReconciliationService:
    def __init__(
        self,
        repository: PortfolioRepository,
        provider: OrderReadProvider,
        clock: Callable[[], datetime],
        minimum_interval: timedelta = timedelta(seconds=30),
    ) -> None:
        if minimum_interval < timedelta(seconds=30):
            raise ValueError("Reconciliation interval must be at least 30 seconds")
        self._repository = repository
        self._provider = provider
        self._clock = clock
        self._minimum_interval = minimum_interval

    async def sync(self, order_id: str) -> StoredOrder:
        return (await self.refresh(order_id, mode="manual")).order

    async def reconcile_unknown(self, order_id: str) -> StoredOrder:
        order = self._require_order(order_id)
        if order.state != OrderState.UNKNOWN:
            return order
        return (await self.refresh(order_id, mode="manual")).order

    def plan_for_orders(
        self, orders: tuple[StoredOrder, ...]
    ) -> tuple[OrderRefreshPlan, ...]:
        groups = tuple(
            (order.provider, order.account_id)
            for order in orders
            if order.state in _SYNCABLE_STATES
        )
        return self._repository.order_refresh_plans(
            groups,
            now=self._now(),
            minimum_interval=self._minimum_interval,
        )

    async def refresh(
        self,
        order_id: str,
        *,
        mode: str = "manual",
    ) -> OrderRefreshResult:
        if mode not in {"manual", "scheduled"}:
            raise ValueError("Invalid refresh mode")
        now = self._now()
        order = self._require_order(order_id)
        if order.state not in _SYNCABLE_STATES:
            return OrderRefreshResult(order, "not_refreshable", False, None, None, now)
        if mode == "scheduled":
            decision = self._repository.claim_planned_order_reconciliation(
                order_id,
                attempt_at=now,
                minimum_interval=self._minimum_interval,
            )
            if decision.claim is None:
                current = self._require_order(order_id)
                return OrderRefreshResult(
                    current,
                    decision.status,
                    False,
                    decision.next_refresh_at,
                    decision.target_order_id,
                    self._now(),
                )
            claim = decision.claim
            next_refresh_at = decision.next_refresh_at
            target_order_id = decision.target_order_id
        else:
            claim = self._repository.claim_order_reconciliation(
                order_id,
                attempt_at=now,
                minimum_interval=self._minimum_interval,
            )
            if claim is None:
                current = self._require_order(order_id)
                return OrderRefreshResult(
                    current,
                    "throttled",
                    False,
                    self._repository.next_order_reconciliation_at(
                        current.provider,
                        current.account_id,
                        now=self._now(),
                        minimum_interval=self._minimum_interval,
                    ),
                    None,
                    self._now(),
                )
            next_refresh_at = now + self._minimum_interval
            target_order_id = order_id
        updated, read_started = await self._execute_claim(claim, now)
        return OrderRefreshResult(
            updated,
            "attempted",
            read_started,
            next_refresh_at,
            target_order_id,
            self._now(),
        )

    def _require_order(self, order_id: str) -> StoredOrder:
        order = self._repository.order(order_id)
        if order is None:
            raise ValueError("Order not found")
        return order

    def _now(self) -> datetime:
        return require_aware_utc(self._clock())

    async def _reconcile(self, order: StoredOrder) -> StoredOrder:
        attempt_at = self._now()
        claim = self._repository.claim_order_reconciliation(
            order.id,
            attempt_at=attempt_at,
            minimum_interval=self._minimum_interval,
        )
        if claim is None:
            return self._require_order(order.id)
        updated, _ = await self._execute_claim(claim, attempt_at)
        return updated

    async def _execute_claim(
        self, claim: ReconciliationClaim, attempt_at: datetime
    ) -> tuple[StoredOrder, bool]:
        provider_read_started = (
            claim.order.broker_order_id is not None
            or claim.order.provider_submission_started_at is not None
        )
        try:
            search = await asyncio.wait_for(
                self._search(claim.order),
                timeout=_PROVIDER_READ_TIMEOUT.total_seconds(),
            )
        except Exception:
            return (
                self._finish_without_match(claim, attempt_at, "provider_error"),
                provider_read_started,
            )
        if not isinstance(search, BrokerOrderSearch):
            return (
                self._finish_without_match(claim, attempt_at, "incomplete"),
                provider_read_started,
            )
        if search.complete is not True:
            return (
                self._finish_without_match(claim, attempt_at, "incomplete"),
                provider_read_started,
            )
        snapshot, outcome = self._single_exact_match(claim.order, search.orders)
        if snapshot is None:
            return (
                self._finish_without_match(claim, attempt_at, outcome),
                provider_read_started,
            )
        return self._finish_match(claim, attempt_at, snapshot), provider_read_started

    async def _search(self, order: StoredOrder) -> BrokerOrderSearch:
        if order.broker_order_id is not None:
            snapshot = await self._provider.find_by_broker_order_id(
                order.account_id, order.broker_order_id
            )
            return (
                BrokerOrderSearch((snapshot,), True)
                if snapshot is not None
                else BrokerOrderSearch((), True)
            )
        started_at = order.provider_submission_started_at
        if started_at is None:
            return BrokerOrderSearch((), True)
        if self._provider.supports_client_order_id_lookup:
            return await self._provider.find_by_client_order_id(
                order.account_id, order.client_order_id
            )
        return await self._provider.search_orders(
            order.account_id,
            started_at - _MATCH_BEFORE,
            started_at + _MATCH_AFTER,
        )

    def _single_exact_match(
        self, order: StoredOrder, snapshots: tuple[BrokerOrderSnapshot, ...]
    ) -> tuple[BrokerOrderSnapshot | None, str]:
        if not isinstance(snapshots, tuple) or any(
            not isinstance(snapshot, BrokerOrderSnapshot) for snapshot in snapshots
        ):
            return None, "incomplete"
        if (
            order.broker_order_id is None
            and order.provider_submission_started_at is None
        ):
            return None, "not_found"
        matches = [snapshot for snapshot in snapshots if self._matches(order, snapshot)]
        matching_by_id: dict[str, BrokerOrderSnapshot] = {}
        for snapshot in matches:
            previous = matching_by_id.get(snapshot.broker_order_id)
            if previous is not None and previous != snapshot:
                return None, "mismatch"
            matching_by_id[snapshot.broker_order_id] = snapshot
        if len(matching_by_id) > 1:
            return None, "ambiguous"
        if len(matching_by_id) == 1:
            return next(iter(matching_by_id.values())), "matched"
        return None, "mismatch" if snapshots else "not_found"

    def _matches(self, order: StoredOrder, snapshot: BrokerOrderSnapshot) -> bool:
        if order.broker_order_id is not None:
            if snapshot.broker_order_id != order.broker_order_id:
                return False
        else:
            started = order.provider_submission_started_at
            if started is None:
                return False
            try:
                submitted_at = require_aware_utc(snapshot.submitted_at)
                started_at = require_aware_utc(started)
            except (AttributeError, TypeError, ValueError):
                return False
            if not (
                started_at - _MATCH_BEFORE <= submitted_at <= started_at + _MATCH_AFTER
            ):
                return False
        return (
            isinstance(snapshot.broker_order_id, str)
            and 0 < len(snapshot.broker_order_id.strip()) <= 128
            and snapshot.account_id == order.account_id
            and snapshot.client_order_id in {None, order.client_order_id}
            and snapshot.instrument_id == order.instrument_id
            and snapshot.side == order.side
            and snapshot.order_type == order.order_type
            and snapshot.quantity == order.quantity
            and snapshot.limit_price == order.limit_price
            and snapshot.time_in_force == "day"
        )

    def _finish_without_match(
        self, claim: ReconciliationClaim, now: datetime, outcome: str
    ) -> StoredOrder:
        state = claim.order.state
        return self._repository.finish_order_reconciliation(
            claim.order.id,
            attempt_id=claim.attempt_id,
            expected_version=claim.order.version,
            expected_state=state,
            state=state,
            now=now,
            outcome=outcome,
            result_code=outcome,
            result_message=_SAFE_MESSAGES.get(outcome, _SAFE_MESSAGES["refused"]),
        )

    def _finish_match(
        self, claim: ReconciliationClaim, now: datetime, snapshot: BrokerOrderSnapshot
    ) -> StoredOrder:
        order = claim.order
        outcome = "matched"
        state = snapshot.state
        if state not in {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }:
            return self._finish_without_match(claim, now, "refused")
        if order.state == OrderState.CANCEL_PENDING and state in {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
        }:
            state = OrderState.CANCEL_PENDING
        updated_at = snapshot.updated_at
        if updated_at is not None:
            try:
                updated_at = require_aware_utc(updated_at)
            except (AttributeError, TypeError, ValueError):
                return self._finish_without_match(claim, now, "mismatch")
            if (
                order.provider_updated_at is not None
                and updated_at < order.provider_updated_at
            ):
                return self._finish_without_match(claim, now, "stale")
        if (
            snapshot.status_label is not None
            and snapshot.status_label not in _ALLOWED_PROVIDER_STATUS_LABELS
        ):
            return self._finish_without_match(claim, now, "mismatch")
        if state == OrderState.FILLED:
            candidate_quantity = (
                snapshot.fill.quantity
                if snapshot.fill is not None
                else order.filled_quantity
            )
            if candidate_quantity != order.quantity:
                return self._finish_without_match(claim, now, "mismatch")
        try:
            return self._repository.finish_order_reconciliation(
                order.id,
                attempt_id=claim.attempt_id,
                expected_version=order.version,
                expected_state=order.state,
                state=state,
                now=now,
                outcome=outcome,
                result_code=outcome,
                result_message=_SAFE_MESSAGES[outcome],
                broker_order_id=snapshot.broker_order_id.strip(),
                fill=snapshot.fill,
                provider_updated_at=updated_at,
                provider_status_label=snapshot.status_label,
                result_source=OrderStatusSource.PROVIDER,
            )
        except (ExecutionError, ValueError):
            return self._finish_without_match(claim, now, "refused")


def format_order_page_response(
    page: OrderPage[StoredOrder],
    reconciliation: OrderReconciliationService | None,
    server_time: datetime,
) -> dict[str, object]:
    plans = reconciliation.plan_for_orders(page.items) if reconciliation else ()
    plans_by_group = {(plan.provider, plan.account_id): plan for plan in plans}
    orders: list[dict[str, object]] = []
    for order in page.items:
        plan = plans_by_group.get((order.provider, order.account_id))
        serialized = order.to_dict()
        serialized["reconciliation"] = {
            "status": order.result_code or "pending",
            "source": (
                order.result_source.value if order.result_source is not None else None
            ),
            "provider_updated_at": (
                order.provider_updated_at.isoformat()
                if order.provider_updated_at is not None
                else None
            ),
            "next_refresh_at": (
                plan.next_refresh_at.isoformat() if plan is not None else None
            ),
            "target_order_id": plan.target_order_id if plan is not None else None,
        }
        orders.append(serialized)

    return {
        "orders": orders,
        "next_cursor": encode_order_cursor(page.next_cursor),
        "refresh_groups": [
            {
                "provider": plan.provider,
                "account_id": plan.account_id,
                "target_order_id": plan.target_order_id,
                "next_refresh_at": plan.next_refresh_at.isoformat(),
            }
            for plan in plans
        ],
        "server_time": require_aware_utc(server_time).isoformat(),
    }
