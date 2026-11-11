import uvicorn

from portfolio_mcp.api import create_app
from portfolio_mcp.bootstrap import (
    create_dashboard_providers,
    create_database_url,
    create_direct_schwab_orders,
    create_market_data_provider,
)
from portfolio_mcp.provider import ProviderConfigurationError
from portfolio_mcp.provider_registry import UnavailablePortfolioProvider

providers = create_dashboard_providers()
try:
    market_data = create_market_data_provider()
except ProviderConfigurationError:
    from portfolio_mcp.provider_registry import UnavailableMarketDataProvider

    market_data = UnavailableMarketDataProvider()

app = create_app(
    providers.get("fixture")
    or providers.get("snaptrade")
    or UnavailablePortfolioProvider(),
    market_data_provider=market_data,
    configured_providers=providers,
    database_url=create_database_url(),
    direct_schwab_orders=create_direct_schwab_orders(),
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
