# Portfolio MCP

Local, read-only MCP server for viewing brokerage accounts, holdings, and
transactions. Currently the only implemented provider is SnapTrade. It has 
been tested using Schwab and Fidelity accounts so far.

## Provider contract

Every read-only provider adapter implements three operations: list accounts,
get a holdings snapshot, and get a date-bounded transaction history. Account
labels must be safe to display, returned transactions are newest first, and a
missing price, market value, or cost basis is represented as `null`.

## Run and verify

```sh
uv run python main.py
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

## Dashboard development

The dashboard frontend lives in `dashboard/` and talks to a local FastAPI API.

```sh
uv run uvicorn api_main:app --reload
cd dashboard && npm install && npm run dev
```

The API listens on `127.0.0.1:8000`; Vite proxies `/api` requests from the
frontend development server. This starts fixture data by default. To use a
configured SnapTrade provider, start the API with:

```sh
uv run --env-file .env uvicorn api_main:app --reload
```

On first launch, select **Refresh portfolio** in the dashboard to save the
provider's current accounts and holdings. The local SQLite database defaults to
the ignored `portfolio.db`; set `PORTFOLIO_DATABASE_URL` to use another SQLite
location.

## SnapTrade configuration

The SnapTrade adapter reads these local environment variables:

```sh
SNAPTRADE_CLIENT_ID=
SNAPTRADE_CONSUMER_KEY=
```

The fixture server is the default. To start the live read-only SnapTrade
provider, export the values and select it explicitly:

```sh
export PORTFOLIO_PROVIDER=snaptrade
export SNAPTRADE_CLIENT_ID='your-client-id'
export SNAPTRADE_CONSUMER_KEY='your-consumer-key'
uv run python main.py
```

Use `PORTFOLIO_PROVIDER=fixture` to return to fictional data. Never commit
`.env` or share credential values.
