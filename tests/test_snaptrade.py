from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from snaptrade_client.exceptions import ApiException

from portfolio_mcp.config import SnapTradeSettings
from portfolio_mcp.provider import (
    AccountNotFoundError,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from portfolio_mcp.snaptrade import SnapTradeProvider


class FakeAccountInformation:
    def __init__(self, error: Exception | None = None) -> None:
        self.activity_offsets: list[int] = []
        self.error = error

    def list_user_accounts(self) -> SimpleNamespace:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            body=[
                {
                    "id": "roth-account",
                    "institution_name": "Fidelity",
                    "name": "Roth IRA",
                    "number": "12345678",
                    "raw_type": "Roth IRA",
                    "account_category": "INVESTMENT",
                },
                {
                    "id": "checking-account",
                    "institution_name": "Fidelity",
                    "name": "Checking",
                    "number": "87654321",
                    "raw_type": "Checking",
                    "account_category": "DEPOSIT",
                },
            ]
        )

    def get_all_account_positions(self, *, account_id: str) -> SimpleNamespace:
        assert account_id == "roth-account"
        return SimpleNamespace(
            body={
                "data_freshness": {"as_of": datetime(2026, 9, 8, 12, 0)},
                "results": [
                    {
                        "instrument": {
                            "symbol": "VTI",
                            "description": "Vanguard Total Stock Market ETF",
                            "kind": "etf",
                            "currency": "USD",
                        },
                        "units": "10.5",
                        "price": "300.25",
                        "cost_basis": "250.00",
                        "currency": "USD",
                    }
                ],
            }
        )

    def get_user_account_details(self, *, account_id: str) -> SimpleNamespace:
        assert account_id == "roth-account"
        return SimpleNamespace(body=self.list_user_accounts().body[0])

    def get_account_activities(
        self,
        *,
        account_id: str,
        start_date: date,
        end_date: date,
        offset: int,
        limit: int,
    ) -> SimpleNamespace:
        assert account_id == "roth-account"
        assert start_date == date(2026, 1, 1)
        assert end_date == date(2026, 12, 31)
        assert limit == 1000
        self.activity_offsets.append(offset)

        if offset == 0:
            return SimpleNamespace(
                body={
                    "data": [
                        {
                            "id": "purchase",
                            "trade_date": date(2026, 1, 2),
                            "type": "BUY",
                            "symbol": {"symbol": "VTI"},
                            "description": "VTI purchase",
                            "units": 2,
                            "amount": -500,
                            "fee": 0,
                            "currency": {"code": "USD"},
                        }
                    ],
                    "pagination": {"total": 2},
                }
            )

        return SimpleNamespace(
            body={
                "data": [
                    {
                        "id": "dividend",
                        "trade_date": date(2026, 2, 1),
                        "type": "DIVIDEND",
                        "amount": 12.34,
                        "fee": 0,
                        "currency": {"code": "USD"},
                    }
                ],
                "pagination": {"total": 2},
            }
        )


class FakeClient:
    def __init__(self, account_information: FakeAccountInformation) -> None:
        self.account_information = account_information


def _provider(
    error: Exception | None = None,
) -> tuple[SnapTradeProvider, FakeAccountInformation]:
    provider = SnapTradeProvider(
        SnapTradeSettings(client_id="client-id", consumer_key="consumer-key")
    )
    account_information = FakeAccountInformation(error)
    setattr(provider, "_client", FakeClient(account_information))
    return provider, account_information


@pytest.mark.asyncio
async def test_get_holdings_maps_a_snapshot() -> None:
    provider, _ = _provider()

    snapshot = await provider.get_holdings("roth-account")

    assert snapshot.account.label == "Fidelity Roth IRA ••••5678"
    assert snapshot.as_of == date(2026, 9, 8)
    assert snapshot.positions[0].symbol == "VTI"
    assert snapshot.positions[0].market_value == Decimal("3152.625")


@pytest.mark.asyncio
async def test_get_transactions_fetches_all_pages_in_reverse_date_order() -> None:
    provider, account_information = _provider()

    history = await provider.get_transactions(
        "roth-account", date(2026, 1, 1), date(2026, 12, 31)
    )

    assert account_information.activity_offsets == [0, 1]
    assert [transaction.id for transaction in history.transactions] == [
        "dividend",
        "purchase",
    ]
    assert history.transactions[0].symbol is None
    assert history.transactions[1].amount == Decimal("-500")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, ProviderAuthenticationError),
        (403, ProviderAuthorizationError),
        (404, AccountNotFoundError),
        (429, ProviderRateLimitError),
        (503, ProviderUnavailableError),
    ],
)
async def test_list_accounts_translates_snaptrade_errors(
    status: int, error_type: type[Exception]
) -> None:
    provider, _ = _provider(ApiException(status=status, reason="ignored"))

    with pytest.raises(error_type):
        await provider.list_accounts()
