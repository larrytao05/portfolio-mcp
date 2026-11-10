# Portfolio verification map

Run the common path in [verify](../SKILL.md), then every sub-feature relevant to the change. Use fixture data and the new isolated run only.

| Feature | Journeys |
| --- | --- |
| [Overview and accounts](overview-accounts.md) | Totals, chart ranges, inspection, gaps/coverage, disclosures, refresh/persistence |
| [Positions](positions.md) | Cross-account ownership, search/account/asset filters, exact sorting, stale/failed loads |
| [Activity](activity.md) | Saved feed, filters, pagination, empty/failure states |
| [Market data](market-data.md) | Search, quotes, unavailable values, retries |
| [Trading safety](trading-safety.md) | Readiness, blocked safeguards, draft validation, fixture-only order review |

A journey without its required fixture/precondition is `BLOCKED`, not passed. The default fixture has nine positions and six activities; it cannot prove a second activity page without extra fixture setup. A single refreshed day cannot prove a multi-day chart. The `--history` run supplies chart gaps and partial coverage; component/API tests cover additional failure states and must be labeled as that evidence.
