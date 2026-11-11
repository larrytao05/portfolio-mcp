from dataclasses import replace
from datetime import date
from typing import Literal

from portfolio_mcp.connection_store import AccountRoute, ConnectionStore
from portfolio_mcp.models import (
    Account,
    AccountCapabilities,
    CapabilityBlock,
    HoldingsSnapshot,
    Instrument,
    Quote,
    TransactionHistory,
)
from portfolio_mcp.provider import (
    AccountNotFoundError,
    BrokerOrderProvider,
    CapabilityProvider,
    PortfolioProvider,
    ProviderUnavailableError,
)
from portfolio_mcp.snaptrade import SnapTradeProvider
from portfolio_mcp.ticker import BrokerOrderHistory


class ProviderRegistry:
    def __init__(
        self, repository: ConnectionStore, providers: dict[str, PortfolioProvider]
    ) -> None:
        self._repository = repository
        self._providers = providers

    def _route(self, account_id: str) -> AccountRoute:
        route = self._repository.account_route(account_id)
        if route is None or not route.active or not route.included:
            raise AccountNotFoundError("Account is not available")
        return route

    def get_portfolio_provider(self, account_id: str) -> PortfolioProvider:
        route = self._route(account_id)
        provider = self._providers.get(route.provider_route)
        if provider is None:
            raise ProviderUnavailableError("Account provider is not configured")
        return provider

    async def _verified_route(self, account_id: str) -> AccountRoute:
        route = self._route(account_id)
        if route.provider_route != "snaptrade":
            return route
        provider = self.get_portfolio_provider(account_id)
        if not isinstance(provider, SnapTradeProvider):
            raise ProviderUnavailableError("SnapTrade connection status is unavailable")
        connections = await provider.connection_accounts()
        expected = route.connection_id.removeprefix("snaptrade:")
        matches = [
            (active, ids)
            for connection_id, active, ids in connections
            if connection_id == expected
        ]
        if (
            not route.connection_id.startswith("snaptrade:")
            or len(matches) != 1
            or not matches[0][0]
            or route.upstream_account_id not in matches[0][1]
        ):
            raise ProviderUnavailableError("SnapTrade connection requires reconnection")
        return route

    async def list_accounts(self) -> list[Account]:
        routes = [
            r for r in self._repository.account_routes() if r.active and r.included
        ]
        if not routes:
            raise ProviderUnavailableError(
                "No included connected accounts are available"
            )
        result: list[Account] = []
        for provider_route in sorted({r.provider_route for r in routes}):
            provider = self._providers.get(provider_route)
            if provider is None:
                raise ProviderUnavailableError("Account provider is not configured")
            accounts = {a.id: a for a in await provider.list_accounts()}
            for route in (r for r in routes if r.provider_route == provider_route):
                await self._verified_route(route.account_id)
                account = accounts.get(route.upstream_account_id)
                if account is None:
                    raise AccountNotFoundError("Mapped account is unavailable")
                result.append(replace(account, id=route.account_id))
        return result

    async def get_account_capabilities(
        self, account_ids: list[str]
    ) -> list[AccountCapabilities]:
        result: list[AccountCapabilities] = []
        for account_id in account_ids:
            route = await self._verified_route(account_id)
            provider = self.get_portfolio_provider(account_id)
            if not isinstance(provider, CapabilityProvider):
                result.append(
                    AccountCapabilities(
                        account_id,
                        route.provider_route,
                        (),
                        (),
                        (),
                        (),
                        (),
                        False,
                        False,
                        None,
                        None,
                        "not_observed",
                        (
                            CapabilityBlock(
                                "capability_unknown", "Trading capability is unknown."
                            ),
                        ),
                        True,
                    )
                )
                continue
            capabilities = await provider.get_account_capabilities(
                [route.upstream_account_id]
            )
            if (
                len(capabilities) != 1
                or capabilities[0].account_id != route.upstream_account_id
            ):
                raise ProviderUnavailableError("Account capability is unavailable")
            result.append(replace(capabilities[0], account_id=account_id))
        return result

    async def get_broker_orders(
        self,
        account_id: str,
        symbol: str,
        status: Literal["open", "closed", "all"] = "all",
    ) -> BrokerOrderHistory:
        route = await self._verified_route(account_id)
        provider = self.get_portfolio_provider(account_id)
        if not isinstance(provider, BrokerOrderProvider):
            raise ProviderUnavailableError("Account order history is unavailable")
        history = await provider.get_broker_orders(
            route.upstream_account_id, symbol, status
        )
        if history.account.id != route.upstream_account_id or any(
            order.account_id != route.upstream_account_id for order in history.orders
        ):
            raise ProviderUnavailableError("Provider returned a different account")
        return replace(
            history,
            account=replace(history.account, id=account_id),
            orders=tuple(
                replace(order, account_id=account_id) for order in history.orders
            ),
        )

    async def get_holdings(self, account_id: str) -> HoldingsSnapshot:
        route = await self._verified_route(account_id)
        snapshot = await self.get_portfolio_provider(account_id).get_holdings(
            route.upstream_account_id
        )
        if snapshot.account.id != route.upstream_account_id or any(
            position.account_id != route.upstream_account_id
            for position in snapshot.positions
        ):
            raise ProviderUnavailableError("Provider returned a different account")
        return replace(
            snapshot,
            account=replace(snapshot.account, id=account_id),
            positions=tuple(
                replace(position, account_id=account_id)
                for position in snapshot.positions
            ),
        )

    async def get_transactions(
        self, account_id: str, start_date: date, end_date: date
    ) -> TransactionHistory:
        route = await self._verified_route(account_id)
        history = await self.get_portfolio_provider(account_id).get_transactions(
            route.upstream_account_id, start_date, end_date
        )
        if history.account.id != route.upstream_account_id or any(
            transaction.account_id != route.upstream_account_id
            for transaction in history.transactions
        ):
            raise ProviderUnavailableError("Provider returned a different account")
        return replace(
            history,
            account=replace(history.account, id=account_id),
            transactions=tuple(
                replace(transaction, account_id=account_id)
                for transaction in history.transactions
            ),
        )


class UnavailablePortfolioProvider:
    async def list_accounts(self) -> list[Account]:
        raise ProviderUnavailableError("Portfolio provider is not configured")

    async def get_holdings(self, account_id: str) -> HoldingsSnapshot:
        raise ProviderUnavailableError("Portfolio provider is not configured")

    async def get_transactions(
        self, account_id: str, start_date: date, end_date: date
    ) -> TransactionHistory:
        raise ProviderUnavailableError("Portfolio provider is not configured")


class UnavailableMarketDataProvider:
    async def search_instruments(self, query: str) -> list[Instrument]:
        raise ProviderUnavailableError("Market data provider is not configured")

    async def get_quote(self, instrument_id: str) -> Quote:
        raise ProviderUnavailableError("Market data provider is not configured")
