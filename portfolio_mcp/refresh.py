from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from portfolio_mcp.database import PortfolioRepository, RefreshResult
from portfolio_mcp.models import Account, AccountCapabilities, CapabilityBlock
from portfolio_mcp.provider import (
    CapabilityProvider,
    PortfolioProvider,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderError,
)


class PortfolioRefreshService:
    def __init__(
        self,
        provider: PortfolioProvider,
        repository: PortfolioRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def refresh(self) -> RefreshResult:
        started_at = self._as_utc(self._clock())
        try:
            accounts = await self._provider.list_accounts()
        except ProviderError as error:
            return self._repository.save_failed_refresh(
                started_at,
                self._as_utc(self._clock()),
                self._refresh_error_code(error),
                str(error),
            )
        except Exception:
            return self._repository.save_failed_refresh(
                started_at,
                self._as_utc(self._clock()),
                "unexpected_error",
                "Portfolio refresh failed",
            )

        snapshots = []
        failed_accounts = []
        transactions = []
        for account in accounts:
            try:
                snapshots.append(await self._provider.get_holdings(account.id))
            except Exception:
                failed_accounts.append(account)
            try:
                history = await self._provider.get_transactions(
                    account.id, date(1970, 1, 1), started_at.date()
                )
                transactions.extend(history.transactions)
            except Exception:
                pass
        completed_at = self._as_utc(self._clock())
        snapshot_date = started_at.astimezone(ZoneInfo("America/New_York")).date()
        capabilities, failed_capability_accounts = await self._capabilities_for(
            accounts
        )
        return self._repository.save_refresh(
            snapshots,
            started_at,
            completed_at,
            snapshot_date,
            failed_accounts,
            transactions,
            capabilities,
            [*failed_accounts, *failed_capability_accounts],
            accounts,
        )

    async def _capabilities_for(
        self, accounts: list[Account]
    ) -> tuple[list[AccountCapabilities], list[Account]]:
        provider = self._provider
        if not isinstance(provider, CapabilityProvider):
            return [self._unknown_capability(account) for account in accounts], []
        try:
            capabilities = await provider.get_account_capabilities(
                [account.id for account in accounts]
            )
            expected_ids = {account.id for account in accounts}
            received_ids = [capability.account_id for capability in capabilities]
            if set(received_ids) != expected_ids or len(received_ids) != len(
                expected_ids
            ):
                return [], accounts
            return capabilities, []
        except Exception:
            return [], accounts

    def _unknown_capability(self, account: Account) -> AccountCapabilities:
        return AccountCapabilities(
            account_id=account.id,
            provider=account.provider,
            asset_classes=(),
            supported_sides=(),
            order_types=(),
            time_in_force=(),
            sizing_modes=(),
            preview_supported=False,
            cancellation_supported=False,
            observed_at=None,
            last_success_at=None,
            source="not_observed",
            blocks=(
                CapabilityBlock("capability_unknown", "Trading capability is unknown."),
            ),
            is_stale=True,
        )

    def _refresh_error_code(self, error: ProviderError) -> str:
        if isinstance(error, ProviderAuthenticationError):
            return "authentication_required"
        if isinstance(error, ProviderAuthorizationError):
            return "authorization_required"
        return "provider_error"

    def _as_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)
