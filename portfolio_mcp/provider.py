from datetime import date
from typing import ClassVar, Protocol, runtime_checkable

from portfolio_mcp.models import (
    Account,
    AccountCapabilities,
    HoldingsSnapshot,
    Instrument,
    Quote,
    TransactionHistory,
)


class ProviderError(ValueError):
    """A client-safe provider failure that never includes secrets or raw payloads."""

    code: ClassVar[str] = "provider_error"


class ProviderConfigurationError(ProviderError):
    code: ClassVar[str] = "provider_configuration_error"


class ProviderAuthenticationError(ProviderError):
    code: ClassVar[str] = "provider_authentication_failed"


class ProviderAuthorizationError(ProviderError):
    code: ClassVar[str] = "provider_authorization_failed"


class ProviderRateLimitError(ProviderError):
    code: ClassVar[str] = "provider_rate_limited"


class ProviderUnavailableError(ProviderError):
    code: ClassVar[str] = "provider_unavailable"


class ProviderResponseError(ProviderError):
    code: ClassVar[str] = "provider_response_error"


class ProviderTLSVerificationError(ProviderUnavailableError):
    code: ClassVar[str] = "provider_tls_verification_failed"

    def __init__(self) -> None:
        super().__init__(
            "Schwab certificate verification failed; repair backend certificate trust"
        )


class ProviderTLSConfigurationError(ProviderConfigurationError):
    code: ClassVar[str] = "provider_tls_configuration_error"

    def __init__(self) -> None:
        super().__init__(
            "Schwab certificate trust configuration is invalid; "
            "repair the configured trust source"
        )


class SchwabReauthorizationRequiredError(ProviderAuthenticationError):
    code: ClassVar[str] = "schwab_reauthorization_required"

    def __init__(self) -> None:
        super().__init__(
            "Schwab rejected the refresh grant; "
            "reauthorize through the Schwab OAuth helper"
        )


class SchwabAuthorizationCodeRejectedError(ProviderAuthenticationError):
    code: ClassVar[str] = "schwab_authorization_code_rejected"

    def __init__(self) -> None:
        super().__init__(
            "Schwab rejected the authorization code; start a new authorization flow"
        )


class SchwabClientAuthenticationError(ProviderAuthenticationError):
    code: ClassVar[str] = "schwab_client_authentication_failed"

    def __init__(self) -> None:
        super().__init__(
            "Schwab rejected client authentication; check the app configuration"
        )


class AccountNotFoundError(ProviderError):
    code: ClassVar[str] = "account_not_found"


class InstrumentNotFoundError(ProviderError):
    code: ClassVar[str] = "instrument_not_found"


class PortfolioProvider(Protocol):
    """The read-only seam between MCP tools and a portfolio data adapter.

    Account labels are safe to display. Snapshots retain unavailable values as
    null rather than inventing data, and transaction histories are inclusive
    of the requested date range in reverse chronological order.
    """

    async def list_accounts(self) -> list[Account]: ...

    async def get_holdings(self, account_id: str) -> HoldingsSnapshot: ...

    async def get_transactions(
        self, account_id: str, start_date: date, end_date: date
    ) -> TransactionHistory: ...


@runtime_checkable
class CapabilityProvider(Protocol):
    async def get_account_capabilities(
        self, account_ids: list[str]
    ) -> list[AccountCapabilities]: ...


class MarketDataProvider(Protocol):
    """The read-only seam for normalized instrument discovery and quotes.

    Search returns no items for an unknown query. Quote price fields are nullable
    when the source recognizes an instrument but cannot currently provide them.
    """

    async def search_instruments(self, query: str) -> list[Instrument]: ...

    async def get_quote(self, instrument_id: str) -> Quote: ...
