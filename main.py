import os
from collections.abc import Mapping

from portfolio_mcp.config import SnapTradeSettings
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.provider import PortfolioProvider, ProviderConfigurationError
from portfolio_mcp.server import create_server
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


mcp = create_server(create_provider())

if __name__ == "__main__":
    mcp.run(transport="stdio")
