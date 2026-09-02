import pytest

from portfolio_mcp.config import SnapTradeSettings
from portfolio_mcp.provider import ProviderConfigurationError


def test_snaptrade_settings_reads_required_values() -> None:
    settings = SnapTradeSettings.from_environment(
        {
            "SNAPTRADE_CLIENT_ID": "client-id",
            "SNAPTRADE_CONSUMER_KEY": "consumer-key",
        }
    )

    assert settings.client_id == "client-id"
    assert settings.consumer_key == "consumer-key"


def test_snaptrade_settings_names_missing_variables_without_values() -> None:
    with pytest.raises(ProviderConfigurationError) as error:
        SnapTradeSettings.from_environment({"SNAPTRADE_CLIENT_ID": "client-id"})

    assert str(error.value) == (
        "Missing required SnapTrade configuration: SNAPTRADE_CONSUMER_KEY"
    )
