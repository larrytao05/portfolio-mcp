from portfolio_mcp.bootstrap import (
    create_database_url,
    create_market_data_provider,
    create_provider,
)
from portfolio_mcp.server import create_server

mcp = create_server(
    create_provider(),
    market_data_provider=create_market_data_provider(),
    database_url=create_database_url(),
)

if __name__ == "__main__":
    mcp.run(transport="stdio")
