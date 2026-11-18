from datetime import UTC, datetime
from decimal import Decimal

import pytest

from portfolio_mcp.alpaca_portfolio import AlpacaPortfolioProvider
from portfolio_mcp.config import AlpacaMarketDataSettings
from portfolio_mcp.provider import AccountNotFoundError, ProviderResponseError


class FakeClient:
    def __init__(self, positions: object) -> None:
        self.positions = positions
        self.urls: list[str] = []

    def request(self, url: str, headers: dict[str, str]) -> tuple[int, object]:
        self.urls.append(url)
        if url.endswith("/account"):
            return 200, {"id": "fictional-id", "currency": "USD", "cash": "12.3400"}
        return 200, self.positions


@pytest.mark.asyncio
async def test_account_and_positions_preserve_decimal_strings_and_nulls() -> None:
    client = FakeClient(
        [
            {
                "symbol": "VTI",
                "asset_class": "us_equity",
                "qty": "1.00000000001",
                "current_price": None,
                "market_value": "123.4500",
                "cost_basis": None,
            }
        ]
    )
    provider = AlpacaPortfolioProvider(
        AlpacaMarketDataSettings("fake", "fake"),
        http_client=client,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    accounts = await provider.list_accounts()
    snapshot = await provider.get_holdings(accounts[0].id)
    assert snapshot.positions[0].asset_class == "equity"
    assert snapshot.positions[0].quantity == Decimal("1.00000000001")
    assert str(snapshot.positions[0].market_value) == "123.4500"
    assert snapshot.positions[0].current_price is None
    cash = snapshot.positions[1]
    assert cash.asset_class == "cash"
    assert str(cash.market_value) == "12.3400"
    assert client.urls[-1] == "https://api.alpaca.markets/v2/positions"
    with pytest.raises(AccountNotFoundError):
        await provider.get_holdings("different-id")


@pytest.mark.asyncio
async def test_malformed_position_fails_closed() -> None:
    client = FakeClient([{"symbol": "VTI", "asset_class": "us_equity", "qty": "NaN"}])
    provider = AlpacaPortfolioProvider(
        AlpacaMarketDataSettings("fake", "fake"), http_client=client
    )
    with pytest.raises(ProviderResponseError):
        await provider.get_holdings("fictional-id")


def test_http_client_refuses_redirects(monkeypatch):
    from email.message import Message
    from urllib.error import HTTPError
    from urllib.request import Request

    from portfolio_mcp.alpaca_market_data import UrllibAlpacaHttpClient

    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == "https://api.alpaca.markets/v2/account"
            raise HTTPError(request.full_url, 302, "redirect", Message(), None)

    def fake_build_opener(handler):
        request = Request(
            "https://api.alpaca.markets/v2/account", headers={"APCA-API-KEY-ID": "fake"}
        )
        assert (
            handler.redirect_request(
                request, None, 302, "redirect", {}, "https://other.invalid/"
            )
            is None
        )
        return FakeOpener()

    monkeypatch.setattr(
        "portfolio_mcp.alpaca_market_data.build_opener", fake_build_opener
    )
    assert UrllibAlpacaHttpClient().request(
        "https://api.alpaca.markets/v2/account", {"APCA-API-KEY-ID": "fake"}
    ) == (302, None)


@pytest.mark.asyncio
async def test_capabilities_require_matching_active_unblocked_account() -> None:
    class AccountClient(FakeClient):
        def __init__(self, body):
            super().__init__([])
            self.body = body

        def request(self, url, headers):
            return 200, self.body

    body = {
        "id": "fictional-id",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
    }
    for fields, permitted in [
        (body, True),
        ({**body, "trading_blocked": True}, False),
        ({k: v for k, v in body.items() if k != "account_blocked"}, False),
    ]:
        source = AlpacaPortfolioProvider(
            AlpacaMarketDataSettings("fake", "fake"), http_client=AccountClient(fields)
        )
        capability = (await source.get_account_capabilities(["fictional-id"]))[0]
        assert capability.is_trade_capable is permitted
    with pytest.raises(AccountNotFoundError):
        await source.get_account_capabilities(["other"])
