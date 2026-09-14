from portfolio_mcp.bootstrap import create_market_data_provider
from portfolio_mcp.schwab_market_data import SchwabMarketDataProvider


def test_market_data_bootstrap_selects_schwab_only_when_explicitly_configured() -> None:
    provider = create_market_data_provider(
        {
            "MARKET_DATA_PROVIDER": "schwab",
            "SCHWAB_CLIENT_ID": "client-id",
            "SCHWAB_CLIENT_SECRET": "client-secret",
            "SCHWAB_REFRESH_TOKEN": "refresh-token",
        }
    )

    assert isinstance(provider, SchwabMarketDataProvider)
