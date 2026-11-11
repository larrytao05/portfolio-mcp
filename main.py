from portfolio_mcp.bootstrap import create_provider
from portfolio_mcp.server import create_server

if __name__ == "__main__":
    mcp = create_server(create_provider())
    mcp.run(transport="stdio")
