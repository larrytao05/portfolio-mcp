# Portfolio MCP plan

## Purpose

Build a local Python MCP server that lets an MCP client inspect a personal
investment portfolio across one or more providers. The server is read-only:
it must never place orders, transfer funds, alter account settings, or write
back to a provider.

The current `main.py` and `transactional_db.py` are the completed ecommerce
tutorial. They are useful as a reference for the MCP wiring, but are not part
of the product. Replace them only once the first portfolio vertical slice is
ready.

## Guiding decisions

- Start with one provider and a small useful tool set. Do not build a generic
  multi-provider framework before a real provider works.
- Treat each provider response as untrusted external input. Normalize and
  validate it before exposing it through MCP tools.
- Make read-only behavior structural: only implement read endpoints and use
  provider credentials with the narrowest available read scope.
- Return structured data for tools whenever practical, rather than formatted
  prose that a client must parse.
- Keep provider-specific code out of the MCP tool layer.
- Keep secrets out of source control, test fixtures, logs, and tool responses.

## Decisions recorded in Milestone 0

- **Deployment:** local, single-user server that others can clone and run on
  their own machines; no hosted or multi-user service.
- **Data sources:** Schwab and Fidelity.
- **Read provider:** SnapTrade Personal API, initially exercised through its
  sandbox and then connected to live accounts with read-only permissions.
- **Future execution:** Schwab only, through a separately approved trading
  path. Fidelity remains read-only. Automated execution is deferred until the
  read-only server, simulation, shadow mode, risk controls, and reconciliation
  are complete.
- **Supported accounts:** taxable brokerage and Roth IRA only.
- **Reporting currency:** USD.
- **Prices:** use SnapTrade-reported valuations in the read-only phase. A
  separate, entitled real-time quote source is required before execution.
- **Persistence:** fetch data on demand; do not create a local portfolio
  database yet.
- **Privacy:** show only safe account labels/masked suffixes; never full
  account numbers in responses or logs.
- **Initial tools:** `list_accounts`, `get_holdings(account_id)`, and
  `get_transactions(account_id, start_date, end_date)`.
- **Quality tools:** Ruff for formatting and linting, Pyright for type checks,
  and pytest for tests.

## Milestone 0 — Product decisions and project baseline

**Outcome:** a concrete first release definition before implementation starts.

- [x] List the portfolio providers to support, ranked by usefulness.
- [x] Choose one provider for the first integration.
- [x] Confirm that its API offers the required read-only endpoints and
  authentication method.
- [x] Identify the first release’s supported account types (for example,
  brokerage, retirement, cash, or crypto).
- [x] Decide the primary base currency and how non-base-currency values are
  represented.
- [x] Decide whether market values come from the provider, a separate market
  data source, or both.
- [x] Write a one-page data policy: local-only vs. hosted usage, retention,
  logging, and whether account identifiers may be displayed.
- [x] Add a `.gitignore` before creating any local environment or secret file.
- [x] Choose formatting, linting, and type-checking tools appropriate for this
  small Python project.
- [x] Record the decisions above in this plan.

**Exit criteria:** the first provider, supported account types, first tools,
and data-handling boundaries are written down.

## Milestone 1 — Replace the tutorial with a portfolio vertical slice

**Outcome:** an MCP server exposes fixture-backed portfolio data without any
external credentials.

- [ ] Create a package layout for application code, tests, provider adapters,
  and domain models.
- [ ] Replace the ecommerce server name with the portfolio server name.
- [ ] Remove the tutorial customer/order/inventory tools and in-memory tables
  once their replacements are in place.
- [ ] Define small domain models for `Account`, `Position`, and `Transaction`.
- [ ] Include provider name and provider account ID in the internal models.
- [ ] Define a safe public account identifier; do not expose full account
  numbers by default.
- [ ] Create deterministic fixture data for one account with a few positions
  and transactions.
- [ ] Implement a fixture-backed repository that returns those models.
- [ ] Add `list_accounts` with a concise, structured response.
- [ ] Add `get_holdings(account_id)` with symbol, quantity, cost basis when
  available, current price/value when available, and currency.
- [ ] Add `get_transactions(account_id, start_date, end_date)` with explicit
  pagination or a conservative response limit.
- [ ] Ensure invalid IDs and invalid dates produce useful, non-sensitive
  errors.
- [ ] Update the existing MCP connection test to assert the portfolio tool
  names and schemas.
- [ ] Add unit tests for fixture data and each tool’s success and error paths.

**Exit criteria:** the MCP inspector/client can call the three tools against
fixtures, and the automated test suite passes without network access.

## Milestone 2 — Provider adapter contract

**Outcome:** provider-specific work has a narrow, testable boundary.

- [x] Define a `PortfolioProvider` interface/protocol with only required read
  operations: accounts, positions, and transactions.
- [x] Decide whether snapshots/valuations belong in the initial interface or
  wait until the first provider proves the need.
- [x] Define provider-specific configuration separately from normalized domain
  models.
- [x] Define a provider exception hierarchy for authentication, authorization,
  rate-limit, unavailable-service, malformed-response, and not-found cases.
- [x] Map those exceptions to client-safe MCP errors without leaking tokens,
  raw response bodies, or account numbers.
- [x] Add a fixture/mock implementation conforming to the new interface.
- [x] Move MCP tools to depend on the interface, never on fixture dictionaries
  or provider SDK objects.
- [x] Add contract tests that every provider implementation must pass.
- [x] Document required fields vs. optional fields, especially cost basis,
  market value, and transaction metadata.

**Exit criteria:** replacing the fixture provider with a real provider changes
only the adapter/configuration wiring, not MCP tool behavior.

## Milestone 3 — First real read-only provider

**Outcome:** the vertical slice reads live data safely from one provider.

- [ ] Read the selected provider’s current API, auth, rate-limit, and sandbox
  documentation.
- [ ] Create read-only credentials with the least privilege available.
- [ ] Store credentials in local environment variables or a local secret
  manager; add a `.env.example` containing names only, never values.
- [ ] Implement configuration validation that identifies missing variables but
  never prints their values.
- [ ] Implement the provider client with explicit connect/read timeouts.
- [ ] Implement the provider’s account listing endpoint.
- [ ] Implement the provider’s positions/holdings endpoint.
- [ ] Implement its transactions/activity endpoint, including pagination.
- [ ] Normalize provider payloads into the project domain models.
- [ ] Preserve the provider’s source timestamp and indicate unavailable fields
  as `null`/absent rather than invented values.
- [ ] Add sanitized logging for request outcome, provider, duration, and error
  category.
- [ ] Add retries only for demonstrably transient, idempotent reads, with a
  small bounded backoff.
- [ ] Compare a sample of returned values against the provider dashboard or an
  export.
- [ ] Add recorded, sanitized response fixtures for adapter tests.
- [ ] Add opt-in integration tests that use live credentials only when an
  explicit environment flag is set.

**Exit criteria:** the three initial tools return verified live data, ordinary
tests remain offline, and no sensitive value appears in repository files or
test output.

## Milestone 4 — Portfolio-friendly read tools

**Outcome:** the MCP interface answers useful investment questions without
forcing the client to reconstruct the portfolio.

- [ ] Decide and document the stable public schemas for existing tools.
- [ ] Add `get_portfolio_summary`, initially for one account and then all
  accounts if aggregation is unambiguous.
- [ ] Include an `as_of` timestamp and data source in every time-sensitive
  response.
- [ ] Add `get_position(symbol, account_id=None)` with explicit behavior for
  multiple matching positions.
- [ ] Add `get_allocation(group_by)` supporting only documented grouping keys
  such as asset class, sector, account, or currency.
- [ ] Return both amount and percentage for allocation rows, with a clear
  denominator.
- [ ] Define how cash, options, funds, fractional shares, and unavailable
  classifications are represented before adding them to calculations.
- [ ] Add filtering and response limits to prevent unexpectedly large tool
  outputs.
- [ ] Write examples for each tool: normal use, no data, and ambiguous lookup.
- [ ] Add calculation tests covering zero values, missing prices, rounding,
  duplicate symbols, and multiple currencies.

**Exit criteria:** a client can inspect accounts, holdings, activity, and a
summary/allocation view using documented, stable schemas.

## Milestone 5 — Data freshness, market data, and performance

**Outcome:** time-sensitive analytics are explicit about what is known and
when it was known.

- [ ] Determine which valuations the provider supplies and their refresh
  cadence.
- [ ] Decide whether an independent market-data provider is necessary.
- [ ] If added, treat market data as a separate read-only adapter with its own
  credentials, rate limits, provenance, and tests.
- [ ] Establish a currency-conversion policy and source for FX rates.
- [ ] Add a short-lived in-memory cache only after measuring a rate-limit or
  latency need.
- [ ] Include freshness metadata for provider data, prices, and FX rates.
- [ ] Define performance methodology before implementing it: period return,
  cash-flow handling, realized vs. unrealized gains, and cost-basis caveats.
- [ ] Implement one clearly defined performance view, not several competing
  metrics.
- [ ] Test calculations using hand-checked examples and documented assumptions.
- [ ] State clearly in tool descriptions that outputs are informational and not
  investment advice.

**Exit criteria:** all market-sensitive values name their source and timestamp;
performance figures have documented methodology and tests.

## Milestone 6 — Additional providers and cross-provider aggregation

**Outcome:** a second provider proves the adapter design and enables a unified
portfolio view.

- [ ] Reassess the first adapter contract using lessons from the live provider.
- [ ] Choose a second provider with genuinely different API/data behavior.
- [ ] Implement the second adapter and satisfy the shared contract tests.
- [ ] Resolve identifier collisions by making provider/account identity
  explicit internally and in tool inputs where necessary.
- [ ] Decide duplicate-holding treatment: retain each account’s position;
  never deduplicate merely because symbols match.
- [ ] Define aggregation rules for currencies, account types, cash, and
  unavailable values.
- [ ] Add an all-accounts summary only when its denominator and freshness are
  well-defined.
- [ ] Add tests with mixed providers, currencies, empty accounts, and partial
  provider failures.
- [ ] Ensure one provider outage returns partial results with clear warnings
  rather than silently omitting data.

**Exit criteria:** two providers work through the same MCP interface, and
cross-provider totals explain included, excluded, and stale data.

## Milestone 7 — Operational hardening

**Outcome:** the server is dependable for routine local use.

- [ ] Add a health/status resource or tool that reports configured providers
  and connectivity state without exposing secrets or account data.
- [ ] Add bounded concurrency so parallel tool calls cannot overwhelm a
  provider.
- [ ] Add request correlation IDs to internal logs.
- [ ] Review all errors and logs for sensitive-data leakage.
- [ ] Add a dependency-update process and periodically review provider SDK/API
  changes.
- [ ] Pin or constrain dependencies according to the project’s update policy.
- [ ] Add CI that runs formatting checks, linting, type checking, unit tests,
  and the MCP connection smoke test.
- [ ] Document local installation, configuration, testing, and MCP client
  registration.
- [ ] Document a credential-rotation procedure.
- [ ] Produce a small release checklist and version the server before sharing
  it with another user/client.

**Exit criteria:** a clean machine can configure the server from documentation,
CI passes, and common failures are diagnosable without exposing private data.

## Deferred until a specific need exists

- Write operations, trading, transfers, or account changes.
- Automated rebalancing or trading recommendations.
- A database or historical data warehouse.
- Background syncing/scheduling.
- Authentication for remote multi-user deployment.
- A web UI.
- Tax-lot accounting and tax advice.

## Suggested execution order

1. Milestone 0
2. Milestone 1
3. Milestone 2
4. Milestone 3
5. Milestone 4
6. Milestone 5 only if live valuations/performance are needed
7. Milestone 6 when a second provider is selected
8. Milestone 7 before regular or shared use

Do not advance solely because all boxes are checked: each milestone’s exit
criteria should be demonstrated through tests and a short manual MCP-client
smoke test.
