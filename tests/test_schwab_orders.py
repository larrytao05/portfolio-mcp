from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from portfolio_mcp.provider import ProviderResponseError
from portfolio_mcp.schwab_orders import SchwabOrderReader


class FakeHttp:
    def __init__(self, body: object):
        self.body = body
        self.url = ""

    def request(self, url: str, headers: dict[str, str]) -> tuple[int, object]:
        self.url = url
        return 200, self.body


def order() -> dict[str, object]:
    return {
        "orderId": 9007199254740993,
        "status": "WORKING",
        "orderType": "LIMIT",
        "price": "12.3000",
        "filledQuantity": "0.5",
        "enteredTime": "2026-11-09T12:00:00Z",
        "orderLegCollection": [
            {
                "legId": 1,
                "instrument": {"symbol": "VTI"},
                "instruction": "BUY",
                "quantity": "2",
            },
            {
                "legId": 2,
                "instrument": {"symbol": "VTI"},
                "instruction": "SELL",
                "quantity": "2",
            },
        ],
    }


@pytest.mark.asyncio
async def test_account_bound_query_preserves_legs_ids_and_truncation():
    client = FakeHttp([order()])
    reader = SchwabOrderReader(
        "canonical", "explicit-hash", "token", http_client=client
    )
    end = datetime(2026, 11, 10, tzinfo=UTC)
    result = await reader.list_orders(end - timedelta(days=90), end, max_results=1)
    assert result.truncated
    assert [leg.order_id for leg in result.orders] == ["9007199254740993"] * 2
    assert [leg.leg_id for leg in result.orders] == ["1", "2"]
    assert all(
        leg.account_id == "canonical" and leg.filled_quantity is None
        for leg in result.orders
    )
    assert result.orders[0].limit_price is None
    assert "/accounts/explicit-hash/orders" in client.url
    assert parse_qs(urlsplit(client.url).query)["maxResults"] == ["1"]


@pytest.mark.asyncio
async def test_invalid_range_and_malformed_orders_fail():
    end = datetime(2026, 11, 10, tzinfo=UTC)
    reader = SchwabOrderReader(
        "canonical", "hash", "token", http_client=FakeHttp([{"orderId": 1}])
    )
    with pytest.raises(ValueError):
        await reader.list_orders(end - timedelta(days=366), end)
    with pytest.raises(ProviderResponseError):
        await reader.list_orders(end - timedelta(days=90), end)
