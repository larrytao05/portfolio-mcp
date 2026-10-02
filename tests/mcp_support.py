from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.execution import FixtureExecutionProvider
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.mcp_authorization import McpAuthorizationService
from portfolio_mcp.refresh import PortfolioRefreshService
from portfolio_mcp.trading_safety import TradingGuard, TradingSettingsService
from portfolio_mcp.trading_service import (
    OrderDraftService,
    OrderSubmissionService,
    fixture_submission_validator,
)


class McpTradingContext(NamedTuple):
    repository: PortfolioRepository
    provider: FixturePortfolioProvider
    market_data: FixtureMarketDataProvider
    drafts: OrderDraftService
    authorization: McpAuthorizationService
    execution: FixtureExecutionProvider
    submission: OrderSubmissionService
    database_url: str


async def setup_mcp_services(tmp_path: Path, now: datetime) -> McpTradingContext:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    repo = PortfolioRepository(db_url, clock=lambda: now)
    provider = FixturePortfolioProvider()
    await PortfolioRefreshService(provider, repo, clock=lambda: now).refresh()

    repo.replace_trading_settings(
        live_trading_enabled=True,
        kill_switch_active=False,
        max_order_shares=Decimal("100"),
        max_order_notional_usd=Decimal("50000"),
        updated_at=now,
        expected_version=0,
    )

    market_data = FixtureMarketDataProvider()
    settings_service = TradingSettingsService(repo, lambda: now)
    guard = TradingGuard(repo, settings_service)
    draft_service = OrderDraftService(repo, market_data, lambda: now, guard)
    mcp_auth_service = McpAuthorizationService(repo, clock=lambda: now, scrypt_n=1024)
    execution_provider = FixtureExecutionProvider(clock=lambda: now)
    validator = fixture_submission_validator(provider)
    submission_service = OrderSubmissionService(
        repository=repo,
        execution_provider=execution_provider,
        clock=lambda: now,
        validator=validator,
        trading_guard=guard,
        mcp_auth_service=mcp_auth_service,
    )

    return McpTradingContext(
        repo,
        provider,
        market_data,
        draft_service,
        mcp_auth_service,
        execution_provider,
        submission_service,
        db_url,
    )
