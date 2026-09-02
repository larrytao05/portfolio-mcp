import os
from dataclasses import dataclass
from typing import Mapping

from portfolio_mcp.provider import ProviderConfigurationError


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
