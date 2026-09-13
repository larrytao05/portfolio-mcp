from datetime import date
from typing import Protocol

from portfolio_mcp.models import (
    Account,
    HoldingsSnapshot,
    Instrument,
    Quote,
    TransactionHistory,
)


class ProviderError(ValueError):
    """A client-safe provider failure that never includes secrets or raw payloads."""

    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderAuthorizationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class AccountNotFoundError(ProviderError):
    pass


class InstrumentNotFoundError(ProviderError):
    pass


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


class MarketDataProvider(Protocol):
    """The read-only seam for normalized instrument discovery and quotes.

    Search returns no items for an unknown query. Quote price fields are nullable
    when the source recognizes an instrument but cannot currently provide them.
    """

    async def search_instruments(self, query: str) -> list[Instrument]: ...

    async def get_quote(self, instrument_id: str) -> Quote: ...
