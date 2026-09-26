import os
from collections.abc import Mapping

from portfolio_mcp.config import (
    ExecutionSettings,
    SchwabMarketDataSettings,
    SchwabSettings,
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
    mode = source.get("PORTFOLIO_PROVIDER", "fixture")

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
    mode = source.get("MARKET_DATA_PROVIDER", "fixture")

    if mode == "fixture":
        return FixtureMarketDataProvider()
    if mode == "schwab":
        settings = SchwabMarketDataSettings.from_environment(source)
        return SchwabMarketDataProvider(settings)

    raise ProviderConfigurationError(
        "MARKET_DATA_PROVIDER must be either 'fixture' or 'schwab'"
    )


def create_database_url(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    return source.get("PORTFOLIO_DATABASE_URL", "sqlite:///portfolio.db")


def create_execution_settings(
    environment: Mapping[str, str] | None = None,
) -> ExecutionSettings:
    return ExecutionSettings.from_environment(environment)


def create_schwab_settings(
    environment: Mapping[str, str] | None = None,
) -> SchwabSettings | None:
    try:
        return SchwabSettings.from_environment(environment)
    except ProviderConfigurationError:
        return None
