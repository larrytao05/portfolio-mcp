# Agent guidance

## Project constraints

- Keep changes focused and simple. Prefer small functions, early returns, and existing modules over new layers. Avoid unnecessary comments and docstrings.
- Preserve user work. Check `git status` and the diff before editing; do not overwrite unrelated changes or treat linked worktrees as disposable.
- Read the relevant issue acceptance criteria and inspect current code/PR state before implementing issue work. Avoid redoing functionality that is already merged.
- Keep portfolio totals and allocations authoritative in the backend. Preserve exact decimal strings; do not recompute financial aggregates in the browser.
- Fail closed for unknown, stale, malformed, or unsupported trading state. Route order actions through the existing shared guard and orchestration; never call an execution provider directly from an API or UI layer.
- Use fixture providers and an isolated SQLite database for local verification. Do not read, print, copy, or modify brokerage credentials or `.env` values. Do not enable live trading or perform real brokerage writes unless the user explicitly authorizes that action.

## Workflow

- Reproduce or trace the relevant behavior before changing it. If an approach fails or reveals a different cause, stop and reassess instead of layering on workarounds.
- Add or update tests for observable behavior, especially failure, stale-data, and safety-boundary cases.
- After changes, run the relevant checks below. For changes spanning backend and frontend, run both sets. Resolve failures before reporting completion.
- Ask before committing or pushing unless the user explicitly requests publication. Do not commit unrelated user changes.

## Verification

Backend checks from the repository root:

```sh
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

Dashboard checks:

```sh
npm --prefix dashboard run typecheck
npm --prefix dashboard run lint
npm --prefix dashboard test -- --run
npm --prefix dashboard run build
```

For local browser verification, follow the fixture-only instructions in `README.md` and `docs/ui-evidence/README.md`; keep its SQLite database isolated from user data.
