# Plan for the remaining dashboard and trading issues

Status: working draft, 2026-09-25. This plan covers the five open feature tracks under [issue #1](https://github.com/larrytao05/portfolio-mcp/issues/1) and their thirteen implementation tickets. It authorizes no live brokerage write.

## Definition of done

The six remaining acceptance checks in issue #1 pass against the running fixture application and an isolated SQLite database. Orders and audit events survive restart. Concurrent or replayed submission and cancellation calls make at most one provider write. Unknown outcomes stay blocked until a unique, exact broker match resolves them. Dashboard and MCP use the same guarded services, with separate one-time authorization for MCP submission and cancellation. Schwab execution stays disabled unless every process, owner, mapping, capability, and entitlement gate permits it. The default test suite makes no brokerage call. All backend and dashboard quality checks pass.

Production readiness also requires confirmed Schwab Accounts and Trading Production access and an opt-in, read-only connectivity check. A real order or cancellation is a separate owner-run validation and is never an automated acceptance test.

## Baseline and delivery rules

The current checkout is `codex/refactor-dashboard-features`, one local commit above the locally recorded `origin/master`. GitHub shows no open pull requests. The checkout contains an untracked `AGENTS.md`, which must be preserved. On this checkout, Ruff, Pyright, 130 backend tests, dashboard typecheck, lint, 28 dashboard tests, and build pass. The owner chose to land the dashboard refactor before issue #48. Refresh the remote state and review that refactor as its own change before starting issue branches.

Use one focused branch and reviewable PR per implementation ticket, as requested. Start each unit from an integrated, verified base. Use fixture providers and a fresh SQLite database. Check the unit's behavior and failure cases before advancing, then run the repository's full backend checks and the dashboard checks when touched. Record each decision and test result in the run's decision log. Do not commit or push without owner approval.

The work is about 29 to 52 engineer-days across thirteen tickets. This is a rough planning range, not a schedule. Schwab entitlement, broker response details, and review feedback can change it. Use high rigor because authorization, concurrency, and live financial writes have high impact.

## Work order

| Order | Tickets | Deliverable and verification gate |
| --- | --- | --- |
| 0 | Baseline | Refresh `master`, settle the dashboard refactor base, and capture the fixture test and pre-change migration baseline. |
| 1 | [#48](https://github.com/larrytao05/portfolio-mcp/issues/48) | Add transactional, append-only allowlisted order events and stable read models. Prove migration on fresh and existing databases, restart, tied-timestamp pagination, old rows without events, and rejection of unsafe details. |
| 2 | [#54](https://github.com/larrytao05/portfolio-mcp/issues/54) | Add lookup, legal transitions, startup recovery, and exact-match reconciliation. Prove zero, one, and multiple matches, failed provider reads, and per-account throttling. No path resubmits an unknown order. |
| 3 | [#41](https://github.com/larrytao05/portfolio-mcp/issues/41) | Expose paginated Orders and Audit APIs and the visible-only dashboard refresh flow. Prove polling stops when hidden or terminal and respects the server's next-refresh time. |
| 4 | [#44](https://github.com/larrytao05/portfolio-mcp/issues/44) | Add durable cancellation intent and one provider cancel call at most. Prove stale versions, partial fills, kill-switch behavior, rejection, concurrency, and unknown outcomes. |
| 5 | [#43](https://github.com/larrytao05/portfolio-mcp/issues/43) | Add independent dashboard cancellation review and lifecycle UI. Prove keyboard access and current-state reload after a conflict. |
| 6 | [#52](https://github.com/larrytao05/portfolio-mcp/issues/52) and [#42](https://github.com/larrytao05/portfolio-mcp/issues/42) | After #12 is complete, build the one-time code store and shared MCP service construction as separate units. Prove code expiry, binding, atomic consumption, and that MCP draft and inspection tools cannot read owner limits or issue codes. |
| 7 | [#55](https://github.com/larrytao05/portfolio-mcp/issues/55) | Add dashboard issuance and MCP submission through the existing guarded submission service. Prove a changed safety context blocks a valid code and a replay never calls the provider. |
| 8 | [#53](https://github.com/larrytao05/portfolio-mcp/issues/53) | Add immutable cancellation requests and action-bound codes. Prove that request creation makes no provider call and submit codes cannot cancel. |
| 9 | [#51](https://github.com/larrytao05/portfolio-mcp/issues/51) | Add MCP cancellation through the shared cancellation service. Prove stale, replayed, concurrent, and unknown flows over MCP stdio. |
| 10 | [#50](https://github.com/larrytao05/portfolio-mcp/issues/50) | After #12 through #15 are complete, add read-only Schwab readiness, shared OAuth transport, and explicit one-to-one account mapping. Confirm entitlement and official API behavior before implementation. No order write is in scope. |
| 11 | [#47](https://github.com/larrytao05/portfolio-mcp/issues/47) | Add the Schwab execution adapter behind the normalized provider contract. Prove exact request translation and error mapping through fake HTTP, including missing broker identity and unsupported orders. |
| 12 | [#46](https://github.com/larrytao05/portfolio-mcp/issues/46) | Wire guarded production selection, safe logs, operations docs, and opt-in read-only readiness. Prove fixture default, fail-closed Schwab configuration, and identical dashboard and MCP service paths. |

Close each feature issue only after its implementation tickets and its parent acceptance criteria pass. Close #1 only after all five tracks meet the definition of done.

## Decisions to settle before implementation

1. The owner reports that Schwab Accounts and Trading Production access is approved and usable. Verify the specific read-only operations and official API behavior before #50. Its implementation still waits until #12 through #15 are complete.
2. Land the reviewed dashboard refactor first, as the owner chose. Keep it separate from the issue #48 PR.
3. The owner chose one implementation ticket per reviewable PR.

Before #54 and #47, settle the execution read contract. `ExecutionProvider.list_recent_orders(client_order_id, limit)` does not express an account-scoped time window when a broker lacks client-ID search. `ExecutionResult` and the stored order also lack several requested fill and provider timestamp fields. Add those fields where their first consumer needs them, without creating a second lifecycle. Before #55, make submission actor an explicit service input; the current repository writes `dashboard-owner` for every submission intent.
