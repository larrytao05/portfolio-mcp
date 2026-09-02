# Portfolio MCP

Local, read-only MCP server for viewing brokerage accounts, holdings, and
transactions. It currently uses fictional fixture data; no real credentials
are needed to run or test it.

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

## SnapTrade configuration

The future SnapTrade adapter will read these local environment variables:

```sh
SNAPTRADE_CLIENT_ID=
SNAPTRADE_CONSUMER_KEY=
```

The fixture server does not read these values yet. When the live adapter is
added, export them in your shell or load them from a local `.env` file before
starting the server. Never commit `.env` or share its values.
