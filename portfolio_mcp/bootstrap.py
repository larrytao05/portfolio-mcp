import json
import os
from collections.abc import Mapping

from portfolio_mcp.alpaca_market_data import AlpacaMarketDataProvider
from portfolio_mcp.config import (
    AlpacaMarketDataSettings,
    SchwabMarketDataSettings,
    SnapTradeSettings,
)
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.provider import (
    MarketDataProvider,
    PortfolioProvider,
    ProviderConfigurationError,
)
from portfolio_mcp.schwab_market_data import SchwabMarketDataProvider
from portfolio_mcp.snaptrade import SnapTradeProvider


def create_provider(
    environment: Mapping[str, str] | None = None,
) -> PortfolioProvider:
    source = os.environ if environment is None else environment
    mode = source.get("PORTFOLIO_PROVIDER", "snaptrade")

    if mode == "fixture":
        return FixturePortfolioProvider()
    if mode == "snaptrade":
        return SnapTradeProvider(SnapTradeSettings.from_environment(source))

    raise ProviderConfigurationError(
        "PORTFOLIO_PROVIDER must be either 'fixture' or 'snaptrade'"
    )


def create_market_data_provider(
    environment: Mapping[str, str] | None = None,
) -> MarketDataProvider:
    source = os.environ if environment is None else environment
    mode = source.get("MARKET_DATA_PROVIDER", "alpaca")

    if mode == "fixture":
        return FixtureMarketDataProvider()
    if mode == "alpaca":
        return AlpacaMarketDataProvider(
            AlpacaMarketDataSettings.from_environment(source)
        )
    if mode == "schwab":
        settings = SchwabMarketDataSettings.from_environment(source)
        return SchwabMarketDataProvider(settings)

    raise ProviderConfigurationError(
        "MARKET_DATA_PROVIDER must be 'fixture', 'alpaca', or 'schwab'"
    )


def create_database_url(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    return source.get("PORTFOLIO_DATABASE_URL", "sqlite:///portfolio.db")


def create_dashboard_providers(
    environment: Mapping[str, str] | None = None,
) -> dict[str, PortfolioProvider]:
    from portfolio_mcp.alpaca_portfolio import AlpacaPortfolioProvider

    source = os.environ if environment is None else environment
    if source.get("PORTFOLIO_PROVIDER") == "fixture":
        return {"fixture": FixturePortfolioProvider()}
    providers: dict[str, PortfolioProvider] = {}
    try:
        providers["alpaca"] = AlpacaPortfolioProvider(
            AlpacaMarketDataSettings.from_environment(source)
        )
    except ProviderConfigurationError:
        pass
    try:
        providers["snaptrade"] = SnapTradeProvider(
            SnapTradeSettings.from_environment(source)
        )
    except ProviderConfigurationError:
        pass
    return providers


def create_direct_schwab_orders(environment: Mapping[str, str] | None = None):
    from portfolio_mcp.direct_schwab_orders import DirectSchwabOrders

    source = os.environ if environment is None else environment
    raw = source.get("SCHWAB_ORDER_ACCOUNT_BINDINGS")
    if not raw:
        return None
    try:
        bindings = json.loads(raw)
    except ValueError:
        raise ProviderConfigurationError(
            "Invalid Schwab order account bindings"
        ) from None
    if not isinstance(bindings, dict) or any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v
        for k, v in bindings.items()
    ):
        raise ProviderConfigurationError("Invalid Schwab order account bindings")
    return DirectSchwabOrders(
        SchwabMarketDataSettings.from_environment(source), bindings
    )
