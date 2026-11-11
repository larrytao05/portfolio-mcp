from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from portfolio_mcp.alpaca_orders import AlpacaOrder
from portfolio_mcp.alpaca_portfolio import AlpacaPortfolioProvider
from portfolio_mcp.connection_store import ConnectionStore
from portfolio_mcp.database import Database
from portfolio_mcp.execution import (
    BrokerOrderSearch,
    BrokerOrderSnapshot,
    ExecutionError,
    FillSummary,
    OrderReadProvider,
    OrderState,
)
from portfolio_mcp.schema import OrderRecord

_STATES = {
    "new": (OrderState.ACCEPTED, "OPEN"),
    "accepted": (OrderState.ACCEPTED, "OPEN"),
    "pending_new": (OrderState.ACCEPTED, "OPEN"),
    "accepted_for_bidding": (OrderState.ACCEPTED, "OPEN"),
    "partially_filled": (OrderState.PARTIALLY_FILLED, "PARTIALLY_FILLED"),
    "filled": (OrderState.FILLED, "FILLED"),
    "rejected": (OrderState.REJECTED, "REJECTED"),
    "pending_cancel": (OrderState.CANCEL_PENDING, "OPEN"),
    "canceled": (OrderState.CANCELED, "CANCELED"),
    "expired": (OrderState.EXPIRED, "EXPIRED"),
}


class AccountOrderReader:
    supports_client_order_id_lookup = True

    def __init__(
        self,
        database: Database,
        alpaca: AlpacaPortfolioProvider,
        fallback: OrderReadProvider | None,
    ):
        self._database = database
        self._routes = ConnectionStore(database)
        self._alpaca = alpaca
        self._fallback = fallback

    def _reader(self, account_id: str):
        route = self._routes.account_route(account_id)
        if route is None or not route.active or not route.included:
            raise ExecutionError("Account order reader is unavailable")
        if (
            route.provider_route == "alpaca"
            and route.credential_reference == "server_env:alpaca"
        ):
            return self._alpaca.order_reader(account_id, route.upstream_account_id)
        if route.provider_route == "fixture" and self._fallback is not None:
            return None
        raise ExecutionError("Account order reader is unavailable")

    def _local(
        self,
        account_id: str,
        *,
        broker_id: str | None = None,
        client_id: str | None = None,
    ) -> OrderRecord:
        with self._database.sessions() as session:
            query = select(OrderRecord).where(OrderRecord.account_id == account_id)
            if broker_id is not None:
                query = query.where(OrderRecord.broker_order_id == broker_id)
            else:
                query = query.where(OrderRecord.client_order_id == client_id)
            records = list(session.scalars(query.limit(2)))
            if len(records) != 1:
                raise ExecutionError("Order identity is unavailable or ambiguous")
            return records[0]

    def _snapshot(self, value: AlpacaOrder, local: OrderRecord) -> BrokerOrderSnapshot:
        if (
            local.provider.casefold() != "alpaca"
            or value.account_id != local.account_id
            or value.client_order_id != local.client_order_id
            or value.symbol != local.symbol
            or value.side != local.side
            or value.order_type != local.order_type
            or value.time_in_force != "day"
            or value.quantity is None
            or value.submitted_at is None
            or value.status not in _STATES
        ):
            raise ExecutionError("Alpaca returned mismatched order details")
        try:
            submitted = datetime.fromisoformat(
                value.submitted_at.replace("Z", "+00:00")
            )
            updated = (
                datetime.fromisoformat(value.updated_at.replace("Z", "+00:00"))
                if value.updated_at
                else None
            )
            if submitted.tzinfo is None or (
                updated is not None and updated.tzinfo is None
            ):
                raise ValueError("Naive order timestamp")
            quantity = Decimal(value.quantity)
            limit = (
                Decimal(value.limit_price) if value.limit_price is not None else None
            )
            filled = (
                Decimal(value.filled_quantity)
                if value.filled_quantity is not None
                else None
            )
            average = (
                Decimal(value.filled_avg_price)
                if value.filled_avg_price is not None
                else None
            )
        except (ValueError, ArithmeticError):
            raise ExecutionError("Alpaca returned invalid order details") from None
        state, label = _STATES[value.status]
        return BrokerOrderSnapshot(
            value.id,
            value.client_order_id,
            value.account_id,
            local.instrument_id,
            local.side,
            local.order_type,
            quantity,
            limit,
            "day",
            submitted,
            state,
            updated,
            label,
            FillSummary(filled, average) if filled is not None and filled > 0 else None,
        )

    async def find_by_broker_order_id(
        self, account_id: str, broker_order_id: str
    ) -> BrokerOrderSnapshot | None:
        reader = self._reader(account_id)
        if reader is None:
            assert self._fallback is not None
            return await self._fallback.find_by_broker_order_id(
                account_id, broker_order_id
            )
        local = self._local(account_id, broker_id=broker_order_id)
        return self._snapshot(await reader.get_order(broker_order_id), local)

    async def find_by_client_order_id(
        self, account_id: str, client_order_id: str
    ) -> BrokerOrderSearch:
        reader = self._reader(account_id)
        if reader is None:
            assert self._fallback is not None
            return await self._fallback.find_by_client_order_id(
                account_id, client_order_id
            )
        local = self._local(account_id, client_id=client_order_id)
        return BrokerOrderSearch(
            (
                self._snapshot(
                    await reader.get_order_by_client_id(client_order_id), local
                ),
            ),
            True,
        )

    async def search_orders(
        self, account_id: str, start_at: datetime, end_at: datetime
    ) -> BrokerOrderSearch:
        raise ExecutionError("Account order search is unavailable")
