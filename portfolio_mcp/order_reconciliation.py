from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from portfolio_mcp.database import (
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
from portfolio_mcp.order_history import OrderStatusSource, require_aware_utc

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
        order = self._require_order(order_id)
        if order.state == OrderState.SUBMITTING:
            return order
        if order.state not in _SYNCABLE_STATES:
            return order
        return await self._reconcile(order)

    async def reconcile_unknown(self, order_id: str) -> StoredOrder:
        order = self._require_order(order_id)
        if order.state != OrderState.UNKNOWN:
            return order
        return await self._reconcile(order)

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
        try:
            search = await self._search(claim.order)
        except Exception:
            return self._finish_without_match(claim, attempt_at, "provider_error")
        if not isinstance(search, BrokerOrderSearch):
            return self._finish_without_match(claim, attempt_at, "incomplete")
        if search.complete is not True:
            return self._finish_without_match(claim, attempt_at, "incomplete")
        snapshot, outcome = self._single_exact_match(claim.order, search.orders)
        if snapshot is None:
            return self._finish_without_match(claim, attempt_at, outcome)
        return self._finish_match(claim, attempt_at, snapshot)

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
