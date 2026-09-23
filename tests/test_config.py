import pytest

from portfolio_mcp.config import SchwabMarketDataSettings, SnapTradeSettings
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


def test_schwab_settings_use_the_default_callback_url_and_read_an_override() -> None:
    environment = {
        "SCHWAB_CLIENT_ID": "client-id",
        "SCHWAB_CLIENT_SECRET": "client-secret",
        "SCHWAB_REFRESH_TOKEN": "refresh-token",
    }

    settings = SchwabMarketDataSettings.from_environment(environment)

    assert settings.callback_url == "https://127.0.0.1:8182"

    overridden_settings = SchwabMarketDataSettings.from_environment(
        {**environment, "SCHWAB_CALLBACK_URL": "https://127.0.0.1:8182"}
    )

    assert overridden_settings.callback_url == "https://127.0.0.1:8182"
