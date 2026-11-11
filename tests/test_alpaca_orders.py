from urllib.parse import parse_qs, urlsplit

import pytest

from portfolio_mcp.alpaca_orders import AlpacaOrderReader
from portfolio_mcp.config import AlpacaMarketDataSettings
from portfolio_mcp.provider import ProviderResponseError


def row(order_id: str, status: str = "partially_filled") -> dict[str, object]:
    return {
        "id": order_id,
        "client_order_id": "client-1",
        "symbol": "VTI",
        "side": "buy",
        "type": "limit",
        "qty": "1.00000001",
        "filled_qty": "0.5",
        "filled_avg_price": "101.2300",
        "limit_price": "102.00",
        "status": status,
        "submitted_at": "2026-11-10T10:00:00Z",
        "canceled_at": None,
    }


class FakeHttp:
    def __init__(self, responses: list[object]):
        self.responses = responses
        self.urls: list[str] = []

    def request(self, url: str, headers: dict[str, str]) -> tuple[int, object]:
        self.urls.append(url)
        if url.endswith("/account"):
            return 200, {"id": "upstream"}
        return 200, self.responses.pop(0)


def reader(client: FakeHttp, account_id: str = "account-a") -> AlpacaOrderReader:
    return AlpacaOrderReader(
        account_id,
        "upstream",
        AlpacaMarketDataSettings("key", "secret"),
        http_client=client,
    )


@pytest.mark.asyncio
async def test_pagination_uses_order_cursor_and_preserves_exact_values():
    client = FakeHttp([[row(str(i)) for i in range(500)], [row("last", "canceled")]])
    orders = await reader(client).list_orders("VTI", "all")
    assert len(orders) == 501
    assert orders[0].account_id == "account-a"
    assert orders[0].quantity == "1.00000001"
    assert orders[0].filled_avg_price == "101.2300"
    assert orders[-1].status == "canceled"
    query = parse_qs(urlsplit(client.urls[2]).query)
    assert query["before_order_id"] == ["499"]
    assert query["status"] == ["all"]
    assert "after" not in query


@pytest.mark.asyncio
async def test_repeated_page_fails_instead_of_returning_incomplete_results():
    page = [row(str(i)) for i in range(500)]
    with pytest.raises(ProviderResponseError, match="repeated"):
        await reader(FakeHttp([page, page])).list_orders("VTI", "open")


@pytest.mark.asyncio
async def test_account_bound_detail_validates_identity():
    first = reader(FakeHttp([row("one")]), "account-a")
    second = reader(FakeHttp([row("one")]), "account-b")
    assert (await first.get_order("one")).account_id == "account-a"
    assert (await second.get_order_by_client_id("client-1")).account_id == "account-b"
    with pytest.raises(ProviderResponseError, match="different"):
        await reader(FakeHttp([row("other")])).get_order("one")


@pytest.mark.asyncio
async def test_unexpected_symbol_and_malformed_decimal_fail_closed():
    bad = row("one") | {"symbol": "OTHER"}
    with pytest.raises(ProviderResponseError, match="symbol"):
        await reader(FakeHttp([[bad]])).list_orders("VTI", "closed")
    bad = row("one") | {"filled_qty": "NaN"}
    with pytest.raises(ProviderResponseError):
        await reader(FakeHttp([bad])).get_order("one")


@pytest.mark.asyncio
async def test_mismatched_account_never_reads_orders():
    from portfolio_mcp.provider import ProviderAuthorizationError

    class WrongAccount(FakeHttp):
        def request(self, url: str, headers: dict[str, str]) -> tuple[int, object]:
            self.urls.append(url)
            return 200, {"id": "different"}

    client = WrongAccount([])
    with pytest.raises(ProviderAuthorizationError):
        await reader(client).list_orders("VTI", "all")
    assert len(client.urls) == 1
    assert client.urls[0].endswith("/account")
