from datetime import date

from mcp.server.mcpserver import MCPServer

from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.provider import PortfolioProvider


def create_server(provider: PortfolioProvider) -> MCPServer:
    mcp = MCPServer("portfolio-mcp")

    @mcp.tool()
    async def list_accounts() -> dict[str, list[dict[str, str]]]:
        """List connected taxable brokerage and Roth IRA accounts."""
        accounts = await provider.list_accounts()
        return {"accounts": [account.to_dict() for account in accounts]}

    @mcp.tool()
    async def get_holdings(account_id: str) -> dict[str, object]:
        """Get holdings and reported valuations for a portfolio account."""
        snapshot = await provider.get_holdings(account_id)
        return snapshot.to_dict()

    @mcp.tool()
    async def get_transactions(
        account_id: str, start_date: str, end_date: str
    ) -> dict[str, object]:
        """Get account transactions in an inclusive ISO-8601 date range."""
        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")
        if start > end:
            raise ValueError("start_date must be on or before end_date")

        history = await provider.get_transactions(account_id, start, end)
        return history.to_dict()

    return mcp


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be an ISO-8601 date (YYYY-MM-DD)"
        ) from error


mcp = create_server(FixturePortfolioProvider())
