import os
from dataclasses import dataclass
from typing import Mapping

from portfolio_mcp.provider import ProviderConfigurationError

DEFAULT_SCHWAB_CALLBACK_URL = "https://127.0.0.1:8182"


@dataclass(frozen=True)
class SnapTradeSettings:
    client_id: str
    consumer_key: str

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "SnapTradeSettings":
        source = os.environ if environment is None else environment
        required = ("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY")
        missing = [name for name in required if not source.get(name)]
        if missing:
            names = ", ".join(missing)
            raise ProviderConfigurationError(
                f"Missing required SnapTrade configuration: {names}"
            )

        return cls(
            client_id=source["SNAPTRADE_CLIENT_ID"],
            consumer_key=source["SNAPTRADE_CONSUMER_KEY"],
        )


@dataclass(frozen=True)
class SchwabMarketDataSettings:
    client_id: str
    client_secret: str
    refresh_token: str
    callback_url: str = DEFAULT_SCHWAB_CALLBACK_URL

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "SchwabMarketDataSettings":
        source = os.environ if environment is None else environment
        required = (
            "SCHWAB_CLIENT_ID",
            "SCHWAB_CLIENT_SECRET",
            "SCHWAB_REFRESH_TOKEN",
        )
        missing = [name for name in required if not source.get(name)]
        if missing:
            raise ProviderConfigurationError(
                f"Missing required Schwab configuration: {', '.join(missing)}"
            )

        return cls(
            client_id=source["SCHWAB_CLIENT_ID"],
            client_secret=source["SCHWAB_CLIENT_SECRET"],
            refresh_token=source["SCHWAB_REFRESH_TOKEN"],
            callback_url=source.get("SCHWAB_CALLBACK_URL", DEFAULT_SCHWAB_CALLBACK_URL),
        )
