import uvicorn

from portfolio_mcp.api import create_app
from portfolio_mcp.bootstrap import create_provider

app = create_app(create_provider())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
