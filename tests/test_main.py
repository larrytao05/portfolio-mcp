import pytest

from main import create_provider
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.provider import ProviderConfigurationError
from portfolio_mcp.snaptrade import SnapTradeProvider


def test_create_provider_requires_live_configuration_by_default() -> None:
    with pytest.raises(ProviderConfigurationError, match="SnapTrade"):
        create_provider({})
    assert isinstance(
        create_provider({"PORTFOLIO_PROVIDER": "fixture"}), FixturePortfolioProvider
    )


def test_create_provider_builds_snaptrade_provider() -> None:
    provider = create_provider(
        {
            "PORTFOLIO_PROVIDER": "snaptrade",
            "SNAPTRADE_CLIENT_ID": "client-id",
            "SNAPTRADE_CONSUMER_KEY": "consumer-key",
        }
    )

    assert isinstance(provider, SnapTradeProvider)


def test_create_provider_rejects_unknown_modes() -> None:
    with pytest.raises(ProviderConfigurationError, match="PORTFOLIO_PROVIDER"):
        create_provider({"PORTFOLIO_PROVIDER": "unsupported"})
