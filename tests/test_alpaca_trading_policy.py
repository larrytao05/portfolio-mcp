from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from portfolio_mcp.alpaca_trading_policy import AlpacaTradingPolicy
from portfolio_mcp.connection_store import ConnectionStore
from portfolio_mcp.database import Database
from portfolio_mcp.models import Instrument, Quote
from portfolio_mcp.stored_orders import OrderDraft
from portfolio_mcp.trading_safety import GuardDecision, GuardViolation
from portfolio_mcp.trading_service import TradingValidationError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def draft():
    return OrderDraft(
        "d",
        "a",
        "Alpaca",
        "Alpaca",
        "us-equity:VTI",
        "VTI",
        "VTI",
        "equity",
        "buy",
        "market",
        Decimal("1"),
        None,
        NOW,
        None,
        Decimal("99"),
        Decimal("100"),
        "iex",
        Decimal("100"),
        NOW,
        NOW,
        NOW,
        (),
        "fingerprint",
        NOW,
        NOW + timedelta(minutes=5),
    )


class Portfolio:
    def __init__(self):
        self.account = {
            "id": "upstream",
            "status": "ACTIVE",
            "trading_blocked": False,
            "account_blocked": False,
            "cash": "101",
            "buying_power": "101",
        }
        self.asset = {
            "symbol": "VTI",
            "class": "us_equity",
            "status": "active",
            "tradable": True,
        }

    async def _get(self, path):
        return self.account if path == "account" else self.asset


class Market:
    def __init__(self):
        self.quote = Quote(
            Instrument("us-equity:VTI", "VTI", "VTI", "equity", "NYSE", "USD"),
            "iex",
            NOW,
            None,
            Decimal("99"),
            Decimal("100"),
            "USD",
            "iex",
            0,
        )

    async def get_execution_quote(self, _instrument):
        return self.quote


class Guard:
    def evaluate(self, intent):
        if intent.ask_price is None:
            return GuardDecision(
                (GuardViolation("quote_unavailable", "Missing ask"),), None
            )
        return GuardDecision((), intent.quantity * intent.ask_price)


def setup(tmp_path):
    repo = ConnectionStore(Database(f"sqlite:///{tmp_path / 'db.sqlite'}"))
    repo.save_connection("c", "alpaca", "live", "server_env:alpaca", True)
    repo.save_account_route("a", "c", "upstream", trade_enabled=True)
    portfolio, market = Portfolio(), Market()
    return (
        repo,
        portfolio,
        market,
        AlpacaTradingPolicy(
            repo,
            cast(Any, portfolio),
            cast(Any, market),
            cast(Any, Guard()),
            lambda: NOW,
        ),
    )


@pytest.mark.asyncio
async def test_fresh_validation_checks_identity_asset_funds_and_quote(tmp_path):
    repo, portfolio, market, policy = setup(tmp_path)
    await policy(draft(), NOW)
    portfolio.account["cash"] = "99.99"
    with pytest.raises(TradingValidationError, match="insufficient"):
        await policy(draft(), NOW)
    portfolio.account["cash"] = "101"
    portfolio.asset["tradable"] = False
    with pytest.raises(TradingValidationError, match="tradable"):
        await policy(draft(), NOW)
    portfolio.asset["tradable"] = True
    portfolio.account["id"] = "other"
    with pytest.raises(TradingValidationError, match="unavailable"):
        await policy(draft(), NOW)
    portfolio.account["id"] = "upstream"
    market.quote = Quote(
        market.quote.instrument,
        "iex",
        NOW - timedelta(seconds=61),
        None,
        Decimal("99"),
        Decimal("100"),
        "USD",
        "iex",
        0,
    )
    with pytest.raises(TradingValidationError, match="current"):
        await policy(draft(), NOW)
    repo.disconnect_connection("c")
    with pytest.raises(TradingValidationError, match="enabled"):
        await policy(draft(), NOW)


@pytest.mark.asyncio
async def test_missing_ask_does_not_use_stale_last_price(tmp_path):
    _, _, market, policy = setup(tmp_path)
    market.quote = Quote(
        market.quote.instrument,
        "iex",
        NOW,
        Decimal("90"),
        Decimal("99"),
        None,
        "USD",
        "iex",
        0,
    )
    with pytest.raises(TradingValidationError, match="Missing ask"):
        await policy(draft(), NOW)
