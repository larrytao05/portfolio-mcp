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

```sh
MARKET_DATA_PROVIDER=schwab
SCHWAB_CLIENT_ID=
SCHWAB_CLIENT_SECRET=
SCHWAB_REFRESH_TOKEN=
```

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

For direct Schwab order reads, configure the existing Schwab OAuth variables
above and explicitly bind each canonical application account ID to its verified
Schwab encrypted account hash. Set `SCHWAB_ORDER_ACCOUNT_BINDINGS` to a JSON
object, for example (both values below are fictional placeholders):

```sh
SCHWAB_ORDER_ACCOUNT_BINDINGS='{"example-application-account-id":"example-verified-encrypted-hash"}'
```

The hash must be verified for the same brokerage account as the canonical
SnapTrade account; account labels are not used for matching. Schwab's account
numbers endpoint returns encrypted hashes to authorized clients. Configure the
mapping on the server without sharing account hashes or credentials. When no
mapping is configured, the account remains on the SnapTrade executed-order
view and direct access requires setup. Direct Schwab history requests cover a
90-day window and identify results that reach the provider limit as potentially
incomplete. These reads do not submit or cancel orders; configuring them does
not enable trading.

For explicit offline development set `PORTFOLIO_PROVIDER=fixture` and
`MARKET_DATA_PROVIDER=fixture`. The verification helper sets both automatically
and always uses an isolated database.

### Guarded Alpaca trading

The dashboard can connect an Alpaca live account using the server's existing Alpaca credentials. Connect Alpaca in Connections and refresh the portfolio. Trading starts blocked. To authorize orders for an account, explicitly enable trading for that account and set the global share and USD-notional limits, enable live trading, and clear the kill switch in Trading safeguards. These controls belong to the account owner.

The supported order scope is long-only US stocks and ETFs, whole shares, market or limit orders, and DAY duration. Market-order reviews use a separate current IEX quote from one exchange, with limited market coverage. Ticker display data remains delayed SIP. A missing, stale, or unauthorized execution quote blocks a market order.

Review the LIVE account and order details before confirmation. Pending or unknown order and cancellation outcomes require reconciliation; do not submit them again. A local disconnect disables the connection and its trading authorization but does not cancel broker orders or revoke remote credentials.
