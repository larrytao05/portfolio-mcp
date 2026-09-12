from portfolio_mcp.bootstrap import create_provider
from portfolio_mcp.server import create_server

mcp = create_server(create_provider())

if __name__ == "__main__":
    mcp.run(transport="stdio")
