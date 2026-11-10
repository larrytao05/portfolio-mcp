---
name: verify
description: Verify Portfolio Dashboard changes through its real React UI and FastAPI API using fictional portfolio, quote, and execution providers in an isolated SQLite database. Use after dashboard or backend changes, before reporting a feature complete, or when asked to test this project.
---

# Verify the Portfolio Dashboard

Use this project workflow for fixture verification. Follow the current `AGENTS.md`. Do not load `.env`, copy brokerage credentials, use the user's database, enable live trading, or call live providers.

## Launch

Run from the repository root. Use installed locked dependencies (`.venv` and `dashboard/node_modules`); if absent, install with `uv sync --locked` and `npm --prefix dashboard ci` under the environment's normal permissions. Never bypass a rejected action.

```sh
python3 .agents/skills/verify/scripts/verify_dashboard.py start
```

This starts the actual FastAPI app and Vite dashboard on unused loopback ports. It uses a new SQLite database with normal migrations, fixture portfolio/market data, fake execution, and the default blocked trading safeguards. Its generated Vite config loads environment files only from the empty run directory, keeping the repository `.env` out of both processes. No existing services are stopped.

Record the exact printed `RUN_DIR`, `DASHBOARD_URL`, and `EVIDENCE_DIR`. Ready means the process identities and port ownership match, API health works, dashboard HTML loads, the dashboard proxy returns the same account list as the API, the overview responds, and trading remains blocked. The account list starts empty; the **Refresh portfolio** user action makes it useful.

For chart changes, use a separate run instead:

```sh
python3 .agents/skills/verify/scripts/verify_dashboard.py start --history
```

This seeds 43 fictional daily snapshots over the preceding 45 days through the repository's normal snapshot persistence. Two missing dates and one partial-coverage date exercise chart breaks. Two fictional accounts hold `VTI` separately. One UI refresh adds the current day, current positions, and fixture activity. Seeding is setup, not evidence that refresh or the UI works. Test the empty state with an unseeded run.

Startup failures automatically stop verified child processes, remove scratch data, and retain the failure reason/logs in `EVIDENCE_DIR`. If local port binding or browser access is blocked, record `BLOCKED`; do not substitute a static mock page or report browser verification as passed.

## Doctor

Before driving, and after any startup concern:

```sh
python3 .agents/skills/verify/scripts/verify_dashboard.py doctor RUN_DIR
```

Substitute the exact path printed by `start`. This is read-only. It verifies recorded process creation times, process-group ownership of both ports, health, HTML, API proxy, overview, and the default blocked safeguards. It rejects unknown runs and mismatched checkout paths. If it fails, stop and diagnose the run; do not click refresh repeatedly or attach to an unrelated existing dashboard.

## Drive

Use the available browser-control skill and its documented driver. In Codex, use CUA browser automation; do not assume standalone Playwright is installed or use it to bypass browser restrictions. Open a **new** tab at `DASHBOARD_URL`, leaving unrelated user tabs alone. Use accessible roles/labels and observe fresh UI state after each action.

Always exercise this common path:

1. Confirm **Portfolio Dashboard**, **API ok**, **Portfolio overview**, and **Positions**. Confirm overview precedes positions and the four secondary panels start collapsed.
2. Click **Refresh portfolio** once. Wait for the completion state. Confirm two fixture accounts, nine positions, and a portfolio value render. Observe the saved record and warnings; an HTTP 200 is insufficient.
3. In the **Positions across accounts** region, confirm separate `VTI` rows identify Schwab and Fidelity. Set **Position account** to one account, then reset it; search **Search positions** for `VTI`, then for a nonmatching symbol. Confirm rows and the no-match state change correctly.
4. Use links in **Primary navigation** to visit **Accounts**, **Activity**, and **Market data**. Confirm each link opens its collapsed panel. Collapse **Account details**, then click **Accounts** again; it must reopen even if the URL hash is unchanged.
5. Under Market data, search **Search instruments** for `VTI`, click **Search**, select the VTI result, and inspect the **Instrument quote** region. Confirm fixture source, instrument identity, observed time, and prices or explicit unavailable values.
6. Reload the dashboard. Confirm the saved accounts and positions survive without a second refresh. This verifies the persisted path alongside the UI.
7. Inspect at 1440×1000 and 375×900 when viewport control exists. Verify the navigation wraps, the document fits the viewport, and wide tables scroll within their own regions. Inspect chart labels/markers, keyboard focus, and uncaught browser errors. Mark unsupported viewport/console/screenshot checks `BLOCKED`, never silently passed.

Then drive every sub-feature relevant to the change in the [feature map](features/README.md). Mocks/interceptions are limited to isolated fixture failure cases and must be labeled separately; keep common refresh, persistence, search, and quote checks on the real API. Never use internal React setters or test-only routes as proof of a user journey.

Run the required code checks and record actual exit results:

```sh
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
npm --prefix dashboard run typecheck
npm --prefix dashboard run lint
npm --prefix dashboard test -- --run
npm --prefix dashboard run build
```

Run backend checks for backend/helper changes, dashboard checks for dashboard changes, both for cross-stack changes. If `uv` cannot access its cache but the locked environment is installed, use these equivalents and record the substitution:

```sh
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
PATH="$PWD/.venv/bin:$PATH" .venv/bin/pytest
```

The explicit interpreter/PATH matters: Pyright and the MCP subprocess tests otherwise may use a different Python environment. Passing code checks does not prove browser behavior.

## Evidence

Use the printed `EVIDENCE_DIR` under `.audit/verification-evidence/`; it is outside the scratch run directory and ignored by Git. Create `verification-summary.md` containing:

- Date, checkout revision, dirty-worktree status, fixture modes, and whether history was seeded.
- Each mapped sub-feature: action, expected state, observed state, and `PASS`, `FAIL`, or `BLOCKED`.
- Commands and exit codes; browser errors; viewport sizes; evidence filenames; limitations.
- Persistence result after reload. For failure tests, identify injected responses and confirm retained data/warnings.

Capture action and resulting state: at minimum desktop overview/positions, one changed filter, and mobile layout when supported. Save screenshots or recordings only through driver-supported capabilities; if the driver can display screenshots but cannot save them, say so. Fixture accounts are fictional and may appear in evidence. Keep any unrelated real data out of captures. Do not write credentials or `.env` content to logs or evidence.

Use a full result of `PASS` only when every required relevant journey was actually driven. Launch failure, missing preconditions, or an unavailable browser leaves the browser proof `BLOCKED`. API/component tests are useful supplemental evidence, not a replacement.

## Cleanup

After success or failure, run:

```sh
python3 .agents/skills/verify/scripts/verify_dashboard.py cleanup RUN_DIR
```

The helper checks the saved checkout/run paths and process creation stamps, signals only its recorded process groups, preserves service logs in the evidence directory, and removes the isolated database/config/cache. It refuses to signal a reused process identity. Never kill by name, port, or an unverified PID. Never remove `EVIDENCE_DIR` during cleanup. Confirm the run directory is gone and `verification-summary.md` plus saved proof still exist. Release the verification browser tab unless the user asked to keep it.

## Helpers

- `scripts/verify_dashboard.py`: executable `start [--history]`, `doctor RUN_DIR`, and `cleanup RUN_DIR` commands above. Requires Python 3.12+, Node, locked project dependencies, and `lsof` on macOS/Linux.
- `scripts/seed_history.py`: executable setup invoked only by `start --history`. Refuses an existing database; writes only a new `portfolio.sqlite` inside a fixture run. Do not run it against user data.
- [Overview and accounts](features/overview-accounts.md), [Positions](features/positions.md), [Activity](features/activity.md), [Market data](features/market-data.md), and [Trading safety](features/trading-safety.md) hold the maintained journeys.

Use `/maintain-verification-skill` when features, selectors, or launch commands change. Update the affected map and prove one real journey again.
