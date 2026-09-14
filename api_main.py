import uvicorn

from portfolio_mcp.api import create_app
from portfolio_mcp.bootstrap import (
    create_database_url,
    create_market_data_provider,
    create_provider,
)

app = create_app(
    create_provider(),
    market_data_provider=create_market_data_provider(),
    database_url=create_database_url(),
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
