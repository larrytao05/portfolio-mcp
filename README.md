# Portfolio Dashboard

A local, read-focused portfolio workspace for viewing brokerage accounts,
holdings, transaction activity, and market quotes. It pairs a React frontend
with a FastAPI backend and SQLite storage, with fixture data available for
offline development.

The dashboard lets you refresh portfolio data, explore account holdings, filter
and page through saved activity, and search instruments and view quotes. It
currently supports SnapTrade for portfolio data and Schwab for market data;
both integrations are strictly read-only.

The guarded order workflow uses fake execution for safe product testing; it
does not submit live orders.

## Guides

- [Frontend guide](dashboard/README.md) explains the React and TypeScript
  application, including data fetching, testing, and a beginner-friendly code
  tour.
- [Backend guide](portfolio_mcp/README.md) explains FastAPI, services,
  providers, SQLite persistence, migrations, and the MCP server.

## Architecture

- `dashboard/` contains the React and TypeScript frontend.
- `api_main.py` starts the FastAPI API used by the dashboard.
- `main.py` starts the MCP server over stdio.
- `portfolio_mcp/` contains provider adapters, application logic, persistence,
  and API routes.
- `tests/` contains backend behavior tests and `alembic/` contains SQLite
  schema migrations.

Portfolio providers list accounts, retrieve holdings snapshots, and return
date-bounded transaction history. Account labels are safe to display,
transactions are newest first, and a missing price, market value, or cost basis
is represented as `null`.

Authoritative cross-account portfolio calculations belong in the backend
overview read model; the browser renders server-provided totals and coverage
rather than rebuilding financial aggregates independently.

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
frontend development server. Portfolio and market-data providers use fixture
data by default. To load a configured provider from `.env`, start the API with:

```sh
uv run --env-file .env uvicorn api_main:app --reload
```

On first launch, select **Refresh portfolio** in the dashboard to save the
provider's current accounts, holdings, and available transaction history. The
local SQLite database defaults to the ignored `portfolio.db`; set
`PORTFOLIO_DATABASE_URL` to use another SQLite location. The dashboard's
Activity section can filter saved transactions by account, provider, type,
symbol, or date range; repeated refreshes do not duplicate activity.

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

## Schwab market-data configuration

The instrument-search and quote workflow uses fixture data unless explicitly
configured otherwise. After completing Schwab's approved production OAuth flow,
add the locally stored credentials and select the Schwab adapter:

`SCHWAB_CALLBACK_URL` defaults to `https://127.0.0.1:8182`; keep the explicit
setting below when it matches the callback URL registered with Schwab.

```sh
MARKET_DATA_PROVIDER=schwab
SCHWAB_CLIENT_ID=
SCHWAB_CLIENT_SECRET=
SCHWAB_REFRESH_TOKEN=
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
```

To replace an expired refresh token, run:

```sh
uv run --env-file .env python -m portfolio_mcp.schwab_oauth
```

Open the printed authorization URL, complete Schwab authorization, and paste the
full redirect URL when prompted. The command prints a replacement
`SCHWAB_REFRESH_TOKEN`; manually replace that value in `.env`.

Start the API with `uv run --env-file .env uvicorn api_main:app --reload`.
This enables only read-only Schwab instrument search and quote requests; it
does not place, preview, cancel, or modify orders. Leave
`MARKET_DATA_PROVIDER=fixture` for offline development and tests. Never
commit `.env` or share credential values.
