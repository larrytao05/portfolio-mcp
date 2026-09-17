# Portfolio MCP backend

`portfolio_mcp/` is the Python application layer behind the dashboard and the
MCP server. It contains the domain model, SQLite repository, external-provider
adapters, refresh and overview services, safe fake-execution workflow, and
FastAPI routes.

## Entry points

- `../api_main.py` creates the FastAPI application for the browser dashboard.
- `../main.py` creates the MCP server for stdio-based MCP clients.
- `api/app.py` defines the HTTP routes and wires services together with the
  repository and configured providers.

Run the HTTP API during dashboard development:

```sh
uv run uvicorn api_main:app --reload
```

Run the MCP server:

```sh
uv run python main.py
```

## Module map

- `models.py` — domain dataclasses such as `Account`, `Position`,
  `Transaction`, `Instrument`, and `Quote`. Decimal financial values are
  serialized as strings at API boundaries.
- `database.py` — `PortfolioRepository`, the only layer that should directly
  read and write SQLite records.
- `refresh.py` — obtains provider snapshots, persists them, and records fresh,
  partial, or failed refresh outcomes.
- `overview.py` — produces the authoritative cross-account overview read model:
  known USD totals, coverage, exclusions, allocations, gain/loss, and recorded
  daily value history.
- `api/app.py` — FastAPI route definitions and error translation.
- `provider.py` — provider protocols and provider-specific error types.
- `snaptrade.py` — SnapTrade portfolio adapter.
- `schwab_market_data.py` — Schwab instrument-search and quote adapter.
- `fixtures.py` — deterministic offline portfolio and market-data providers.
- `bootstrap.py` and `config.py` — select providers and database configuration
  from the environment.
- `trading_service.py` and `execution.py` — validates drafts and performs only
  the guarded fake-order execution path.
- `server.py` — MCP tool server construction.

## Request and persistence flow

```text
FastAPI route
  -> service
  -> PortfolioRepository
  -> SQLite

refresh route
  -> configured provider
  -> refresh service
  -> PortfolioRepository
  -> SQLite
```

Routes should stay thin: validate HTTP input, call a service or repository, and
serialize the result. Business rules belong in services. SQLAlchemy persistence
details belong in `PortfolioRepository`.

## Data and migrations

SQLite is the local source of saved portfolio state. The default database URL
points at the ignored `portfolio.db`; set `PORTFOLIO_DATABASE_URL` to override
it. Alembic migration configuration is at the repository root:

- `../alembic/env.py` — migration environment.
- `../alembic/versions/` — ordered schema migrations.

Persisted records include accounts, positions, transactions, refresh outcomes,
daily account values, and fake-order records. Missing prices, market values,
and cost bases remain `None`/`null` rather than becoming zero.

## Providers and secrets

Fixture providers are the safe default. Optional live integrations are
read-only:

```sh
PORTFOLIO_PROVIDER=snaptrade
SNAPTRADE_CLIENT_ID=
SNAPTRADE_CONSUMER_KEY=

MARKET_DATA_PROVIDER=schwab
SCHWAB_CLIENT_ID=
SCHWAB_CLIENT_SECRET=
SCHWAB_REFRESH_TOKEN=
```

Use an uncommitted `.env` file with `uv run --env-file .env ...`. Never commit
credentials or provider payloads containing private account data.

## Tests and quality checks

Backend tests live in `../tests/`. They cover API routes, refresh persistence,
overview aggregation, provider contracts, market data, execution, and server
behavior.

```sh
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

Run these checks after backend changes. The project targets Python 3.12 and
uses Ruff for formatting/linting, Pyright for static type checks, and Pytest
for behavior tests.
