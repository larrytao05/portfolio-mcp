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

The concrete Schwab HTTPS regression tests require the `openssl` executable.
They generate their temporary test certificates and keys outside the repository.

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

If a Schwab request fails after restarting the backend, use the reported error
to choose the recovery step:

- `provider_tls_verification_failed`: repair the backend's certificate trust
  or network inspection configuration. Reauthorizing the Schwab account does
  not fix certificate verification.
- `provider_tls_configuration_error`: repair the configured `SSL_CERT_FILE` or
  `SSL_CERT_DIR` source, or remove an unintended override. Public Schwab HTTPS
  also uses the maintained certifi bundle alongside OpenSSL's default roots.
- `schwab_reauthorization_required`: Schwab rejected the refresh grant as
  `invalid_grant`; run the owner-operated OAuth helper above and replace the
  local refresh token.
- `schwab_client_authentication_failed`: check that the Schwab app's client ID
  and secret are correct and belong to the approved app.
- `provider_authorization_failed`: verify the app's API permissions and
  entitlements. A new refresh token may not change these permissions.
- `provider_unavailable` or `provider_rate_limited`: retry after the temporary
  outage or rate limit has cleared.

An anonymous HTTPS response can help diagnose certificate connectivity without
using account credentials:

```sh
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' https://api.schwabapi.com/
```

Any HTTP response, including an error such as 404, confirms only that DNS,
network connectivity, and TLS completed for that request. It does not validate
the configured client credentials or refresh token.

Start the API with `uv run --env-file .env uvicorn api_main:app --reload`.
This enables only read-only Schwab instrument search and quote requests; it
does not place, preview, cancel, or modify orders. Leave
`MARKET_DATA_PROVIDER=fixture` for offline development and tests. Never
commit `.env` or share credential values.

## Isolated browser verification

Use fictional providers and a separate SQLite database for UI checks:

```sh
verification_dir="$(mktemp -d)"
export PORTFOLIO_DATABASE_URL="sqlite:///$verification_dir/portfolio.db"
export PORTFOLIO_PROVIDER=fixture
export MARKET_DATA_PROVIDER=fixture
export EXECUTION_PROVIDER=fixture
export SCHWAB_EXECUTION_ENABLED=false
uv run uvicorn api_main:app --host 127.0.0.1 --port 8000
```

In another terminal, run `npm --prefix dashboard run dev`. Open the printed
localhost URL and refresh the portfolio. Check Overview, Accounts, Activity,
Market data, Orders, and Settings. Search for VTI and confirm that its quote
shows Fixture market data. Order actions use the fixture execution provider.

Stop both servers when finished and remove the temporary directory printed by
`echo "$verification_dir"`. Attach screenshots and recordings directly to PR
descriptions. Keep them outside the repository.
