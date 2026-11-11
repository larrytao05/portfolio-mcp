# Portfolio Dashboard

A local, read-focused portfolio workspace for viewing brokerage accounts,
holdings, transaction activity, and market quotes. It pairs a React frontend
with a FastAPI backend and SQLite storage, with fixture data available for
offline development.

The dashboard lets you refresh portfolio data, explore account holdings, filter
and page through saved activity, and search instruments and view quotes. It
currently supports SnapTrade for portfolio data, Alpaca delayed SIP market data,
and optional Schwab quotes;
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

For repeatable UI verification, use the project-local
[verify skill](.agents/skills/verify/SKILL.md). It provides an isolated,
fixture-only launcher, a read-only readiness check, browser journeys for each
feature, retained evidence, and cleanup. It does not load brokerage `.env`
values or use the normal portfolio database.

```sh
python3 .agents/skills/verify/scripts/verify_dashboard.py start --history
```

Use the printed dashboard URL and run directory with the skill's doctor,
browser, and cleanup steps. Local server permissions and browser access are
required to complete the browser proof; a blocked launch is not a UI pass.

The concrete Schwab HTTPS regression tests require the `openssl` executable.
They generate their temporary test certificates and keys outside the repository.

## Dashboard development

The dashboard frontend lives in `dashboard/` and talks to a local FastAPI API.

```sh
uv run uvicorn api_main:app --reload
cd dashboard && npm install && npm run dev
```

The API listens on `127.0.0.1:8000`; Vite proxies `/api` requests from the
frontend development server. Normal startup selects live SnapTrade portfolio reads and Alpaca market data.
Missing credentials produce a configuration error. Configure them locally, then
start the API with:

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

SnapTrade is the normal portfolio provider. To start the live read-only
provider, configure the values locally:

```sh
export PORTFOLIO_PROVIDER=snaptrade
export SNAPTRADE_CLIENT_ID='your-client-id'
export SNAPTRADE_CONSUMER_KEY='your-consumer-key'
uv run python main.py
```

Use `PORTFOLIO_PROVIDER=fixture` to return to fictional data. Never commit
`.env` or share credential values.

## Schwab market-data configuration

Alpaca delayed SIP is the normal market-data provider. Schwab remains available
for instrument search and quotes but does not provide this app’s intraday chart. After completing Schwab's approved production OAuth flow,
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
These settings provide read-only Schwab instrument search and quote requests;
order reads require the separate account mapping described below. Leave
`MARKET_DATA_PROVIDER=fixture` for offline development and tests. Never
commit `.env` or share credential values.

## Alpaca ticker workspace

Configure `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` locally and use
`MARKET_DATA_PROVIDER=alpaca` (the default). Credentials remain server-side.
The adapter uses live Alpaca asset discovery, delayed-SIP quote snapshots,
and historical SIP one-minute bars ending at least 15 minutes before now.
It never silently substitutes IEX. Feed entitlement errors are shown explicitly.
These market-data requests do not submit orders.

Select a ticker under Market data to view today's candles and volume, inspect
exact OHLC values with hover or the keyboard slider, include extended hours,
and view bid/ask quotes. The selected instrument is retained in the URL.
Quotes update every 15 seconds; bars and order reads every minute while the
page is visible. These are ticker prices, separate from daily portfolio snapshots.

Positions below the chart come from saved portfolio data. The order views use
account-scoped reads: Alpaca reads open and historical orders directly, while
the SnapTrade integration imports executed orders from a requested 90-day
window. SnapTrade completeness and source sync time are unknown; Fidelity data
may update daily with a delay of up to 24 hours. Open orders require direct
access. Each account reports its source, coverage and any retrieval failure.

For direct Schwab order reads, configure the Schwab OAuth variables above and
confirm the account mapping in the dashboard. The mapping links a canonical
portfolio account to an owner-verified Schwab account; labels do not establish
identity. Without a verified mapping, the account remains on the SnapTrade
executed-order view. Direct Schwab requests cover a 90-day window and mark a
provider-limit result as potentially incomplete. These reads do not enable
trading. This project does not currently ship a direct Schwab execution adapter.

For explicit offline development set `PORTFOLIO_PROVIDER=fixture` and
`MARKET_DATA_PROVIDER=fixture`. The verification helper sets both automatically
and always uses an isolated database.

### Guarded Alpaca trading

The dashboard can connect an Alpaca live account using the server's existing Alpaca credentials. Connect Alpaca in Connections and refresh the portfolio. Trading starts blocked. To authorize orders for an account, explicitly enable trading for that account and set the global share and USD-notional limits, enable live trading, and clear the kill switch in Trading safeguards. These controls belong to the account owner.

The supported order scope is long-only US stocks and ETFs, whole shares, market or limit orders, and DAY duration. Market-order reviews use a separate current IEX quote from one exchange, with limited market coverage. Ticker display data remains delayed SIP. A missing, stale, or unauthorized execution quote blocks a market order.

Review the LIVE account and order details before confirmation. Pending or unknown order and cancellation outcomes require reconciliation; do not submit them again. A local disconnect disables the connection and its trading authorization but does not cancel broker orders or revoke remote credentials.

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
