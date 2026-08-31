from datetime import date
from typing import Protocol

from portfolio_mcp.models import Account, Position, Transaction


class AccountNotFoundError(ValueError):
    pass


class PortfolioProvider(Protocol):
    as_of: date

    async def list_accounts(self) -> list[Account]: ...

    async def get_holdings(self, account_id: str) -> list[Position]: ...

    async def get_transactions(
        self, account_id: str, start_date: date, end_date: date
    ) -> list[Transaction]: ...
