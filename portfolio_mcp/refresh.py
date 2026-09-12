from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from portfolio_mcp.database import PortfolioRepository, RefreshResult
from portfolio_mcp.provider import PortfolioProvider, ProviderError


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
            snapshots = [
                await self._provider.get_holdings(account.id) for account in accounts
            ]
        except ProviderError as error:
            self._repository.save_failed_refresh(
                started_at,
                self._as_utc(self._clock()),
                "provider_error",
                str(error),
            )
            raise
        except Exception:
            self._repository.save_failed_refresh(
                started_at,
                self._as_utc(self._clock()),
                "unexpected_error",
                "Portfolio refresh failed",
            )
            raise
        completed_at = self._as_utc(self._clock())
        snapshot_date = started_at.astimezone(ZoneInfo("America/New_York")).date()
        return self._repository.save_refresh(
            snapshots,
            started_at,
            completed_at,
            snapshot_date,
        )

    def _as_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)
