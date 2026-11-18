from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from portfolio_mcp.account_order_reader import AccountOrderReader
from portfolio_mcp.alpaca_portfolio import AlpacaPortfolioProvider
from portfolio_mcp.config import AlpacaMarketDataSettings
from portfolio_mcp.connection_store import ConnectionStore
from portfolio_mcp.database import Database
from portfolio_mcp.execution import ExecutionError, OrderState
from portfolio_mcp.order_reconciliation import OrderReconciliationService
from portfolio_mcp.provider import ProviderAuthorizationError
from portfolio_mcp.schema import AccountRouteRecord, OrderRecord
from tests.test_order_reconciliation import create_unknown_order


class FakeAlpacaHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.account = "upstream"

    def request(self, url, headers):
        self.calls.append(url)
        if url.endswith("/v2/account"):
            return 200, {"id": self.account}
        return self.response


@pytest.mark.asyncio
async def test_unknown_alpaca_order_reconciles_by_exact_client_id_and_is_throttled(
    tmp_path,
):
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'orders.sqlite'}")
    order = await create_unknown_order(database, now)
    routes = ConnectionStore(database)
    routes.save_connection("live", "alpaca", "live", "server_env:alpaca", True)
    with database.sessions.begin() as session:
        session.execute(
            update(AccountRouteRecord)
            .where(AccountRouteRecord.account_id == order.account_id)
            .values(connection_id="live", upstream_account_id="upstream")
        )
        session.execute(
            update(OrderRecord)
            .where(OrderRecord.id == order.id)
            .values(provider="Alpaca")
        )
    body = {
        "id": "broker-1",
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "type": order.order_type,
        "qty": str(order.quantity),
        "filled_qty": "0",
        "limit_price": str(order.limit_price),
        "status": "new",
        "time_in_force": "day",
        "submitted_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    http = FakeAlpacaHttp((200, body))
    source = AlpacaPortfolioProvider(
        AlpacaMarketDataSettings("synthetic", "synthetic"), http_client=http
    )
    reader = AccountOrderReader(database, source, None)
    service = OrderReconciliationService(database, reader, lambda: now)
    refreshed = await service.refresh(order.id)
    assert refreshed.order.state == OrderState.ACCEPTED
    assert refreshed.order.broker_order_id == "broker-1"
    assert "/orders:by_client_order_id?" in http.calls[-1]
    calls = len(http.calls)
    assert (await service.refresh(order.id)).status == "throttled"
    assert len(http.calls) == calls
    with pytest.raises(ExecutionError):
        await reader.find_by_client_order_id("different-account", order.client_order_id)
    assert len(http.calls) == calls


@pytest.mark.asyncio
async def test_alpaca_reader_rejects_wrong_upstream_or_client_identity(tmp_path):
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    database = Database(f"sqlite:///{tmp_path / 'orders.sqlite'}")
    order = await create_unknown_order(database, now)
    routes = ConnectionStore(database)
    routes.save_connection("live", "alpaca", "live", "server_env:alpaca", True)
    with database.sessions.begin() as session:
        session.execute(
            update(AccountRouteRecord)
            .where(AccountRouteRecord.account_id == order.account_id)
            .values(connection_id="live", upstream_account_id="upstream")
        )
        session.execute(
            update(OrderRecord)
            .where(OrderRecord.id == order.id)
            .values(provider="Alpaca")
        )
    http = FakeAlpacaHttp(
        (200, {"id": "broker", "client_order_id": "wrong", "status": "new"})
    )
    source = AlpacaPortfolioProvider(
        AlpacaMarketDataSettings("synthetic", "synthetic"), http_client=http
    )
    service = OrderReconciliationService(
        database, AccountOrderReader(database, source, None), lambda: now
    )
    result = await service.reconcile_unknown(order.id)
    assert result.state == OrderState.UNKNOWN
    assert result.result_code == "provider_error"
    assert len(http.calls) == 2
    http.account = "wrong-upstream"
    with pytest.raises(ProviderAuthorizationError):
        await AccountOrderReader(database, source, None).find_by_client_order_id(
            order.account_id, order.client_order_id
        )
    assert len(http.calls) == 3
