from datetime import UTC, datetime

import pytest

from portfolio_mcp.database import PortfolioRepository


def test_routes_persist_and_disconnect_without_deleting_history(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'portfolio.sqlite'}"
    repository = PortfolioRepository(database_url)
    repository.save_connection(
        "fixture-connection", "fixture", "fixture", "fixture", True
    )
    repository.save_account_route("account-1", "fixture-connection", "upstream-1")
    route = PortfolioRepository(database_url).account_route("account-1")
    assert route is not None
    assert route.active and route.included and not route.trade_enabled
    repository.save_connection(
        "fixture-connection", "fixture", "fixture", "fixture", False
    )
    route = repository.account_route("account-1")
    assert route is not None and not route.active
    assert repository.account_route("missing") is None
    with pytest.raises(ValueError):
        repository.save_account_route("account-1", "fixture-connection", "other")


@pytest.mark.asyncio
async def test_registry_rejects_unknown_hidden_and_disconnected_accounts(tmp_path):
    from portfolio_mcp.fixtures import FixturePortfolioProvider
    from portfolio_mcp.provider import AccountNotFoundError
    from portfolio_mcp.provider_registry import ProviderRegistry

    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.sqlite'}")
    provider = FixturePortfolioProvider()
    registry = ProviderRegistry(repository, {"fixture": provider})
    repository.save_connection("fixture", "fixture", "fixture", "fixture", True)
    repository.save_account_route("canonical", "fixture", "schwab-taxable-demo")
    assert (await registry.list_accounts())[0].id == "canonical"
    snapshot = await registry.get_holdings("canonical")
    assert snapshot.positions[0].account_id == "canonical"
    now = datetime.now(UTC)
    repository.save_refresh([snapshot], now, now, now.date())
    with pytest.raises(AccountNotFoundError):
        registry.get_portfolio_provider("unknown")
    repository.save_account_route(
        "canonical", "fixture", "schwab-taxable-demo", included=False
    )
    from portfolio_mcp.provider import ProviderUnavailableError

    with pytest.raises(ProviderUnavailableError):
        await registry.list_accounts()
    with pytest.raises(AccountNotFoundError):
        await registry.get_holdings("canonical")
    repository.save_account_route("canonical", "fixture", "schwab-taxable-demo")
    repository.save_connection("fixture", "fixture", "fixture", "fixture", False)
    with pytest.raises(AccountNotFoundError):
        await registry.get_holdings("canonical")
    assert repository.list_positions("canonical") is not None
    assert repository.daily_values("canonical")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["holdings", "transactions", "orders"])
@pytest.mark.parametrize("part", ["parent", "child"])
async def test_registry_rejects_mismatched_provider_ownership(tmp_path, method, part):
    from dataclasses import replace
    from datetime import date

    from portfolio_mcp.fixtures import FixturePortfolioProvider
    from portfolio_mcp.provider import ProviderUnavailableError
    from portfolio_mcp.provider_registry import ProviderRegistry

    class WrongAccount(FixturePortfolioProvider):
        async def get_holdings(self, account_id):
            result = await super().get_holdings(account_id)
            if method != "holdings":
                return result
            return (
                replace(result, account=replace(result.account, id="wrong"))
                if part == "parent"
                else replace(
                    result,
                    positions=(replace(result.positions[0], account_id="wrong"),),
                )
            )

        async def get_transactions(self, account_id, start_date, end_date):
            result = await super().get_transactions(account_id, start_date, end_date)
            if method != "transactions":
                return result
            return (
                replace(result, account=replace(result.account, id="wrong"))
                if part == "parent"
                else replace(
                    result,
                    transactions=(replace(result.transactions[0], account_id="wrong"),),
                )
            )

        async def get_broker_orders(self, account_id, symbol, status="all"):
            result = await super().get_broker_orders(account_id, symbol, status)
            if method != "orders":
                return result
            return (
                replace(result, account=replace(result.account, id="wrong"))
                if part == "parent"
                else replace(
                    result, orders=(replace(result.orders[0], account_id="wrong"),)
                )
            )

    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.sqlite'}")
    repository.save_connection("fixture", "fixture", "fixture", "fixture", True)
    repository.save_account_route("canonical", "fixture", "schwab-taxable-demo")
    registry = ProviderRegistry(repository, {"fixture": WrongAccount()})
    with pytest.raises(ProviderUnavailableError):
        if method == "holdings":
            await registry.get_holdings("canonical")
        elif method == "transactions":
            await registry.get_transactions(
                "canonical", date(1970, 1, 1), date(2030, 1, 1)
            )
        else:
            await registry.get_broker_orders("canonical", "VTI")


def test_hiding_and_disconnecting_clear_trade_permission(tmp_path):
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.sqlite'}")
    repository.save_connection("fixture", "fixture", "fixture", "fixture", True)
    repository.save_account_route("account", "fixture", "upstream", trade_enabled=True)
    repository.save_account_route(
        "account", "fixture", "upstream", included=False, trade_enabled=True
    )
    repository.save_account_route("account", "fixture", "upstream", included=True)
    route = repository.account_route("account")
    assert route is not None and not route.trade_enabled
    repository.save_account_route("account", "fixture", "upstream", trade_enabled=True)
    assert repository.disconnect_connection("fixture")
    repository.save_connection("fixture", "fixture", "fixture", "fixture", True)
    route = repository.account_route("account")
    assert route is not None and not route.trade_enabled


def test_stale_enable_after_hide_is_rejected(tmp_path):
    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.sqlite'}")
    repository.save_connection("fixture", "fixture", "fixture", "fixture", True)
    repository.save_account_route("account", "fixture", "upstream", trade_enabled=True)
    assert repository.change_account_settings("account", 0, included=False)
    assert not repository.change_account_settings("account", 0, trade_enabled=True)
    assert repository.change_account_settings("account", 1, included=True)
    assert not repository.change_account_settings("account", 0, trade_enabled=True)
    route = repository.account_route("account")
    assert route is not None and not route.trade_enabled
