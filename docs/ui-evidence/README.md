# Dashboard design verification

This evidence covers #25, #26 and #27 under the visual direction in #24.
All screenshots and the recording use fictional fixture accounts. State
screenshots use intercepted fixture API responses to exercise failures without
requiring a brokerage outage. No credentials or live account data are included.

## Screenshots and recording

- [Desktop viewport](desktop-viewport.png) and [full desktop page](desktop.png).
- [Mobile header at 375px](mobile-header.png), [mobile account](mobile-viewport.png)
  and [full mobile page](mobile.png).
- [Instrument search and quote](market-data.png).
- [Partial refresh](partial-refresh.png) and [failed refresh](failed-refresh.png).
- [Empty record](empty.png), [loading record](loading.png),
  [unavailable record](unavailable-record.png) and [unavailable quote](unavailable-quote.png).
- [Dashboard demo video](dashboard-demo.webm): refresh, filter and sort holdings,
  filter activity, search/select a quote, then inspect narrow-screen behavior.

## Verification

The frontend typecheck, ESLint, production build and all 15 component tests pass.
Backend Ruff formatting/lint, Pyright and all 42 pytest tests pass.

Chromium checks against the production build exercised refresh, holdings
filtering/sorting, activity filtering, instrument search/quotes, anchor
navigation and keyboard focus with no page runtime errors. At 375px the
document width equals the viewport; holdings overflow only within the table
container. The identity column stays stationary while scrolling the table.
The initial keyboard focus has a visible 2px outline.

WCAG 2 A/AA and 2.1 AA axe scans found zero violations at 1440px and 375px.
These automated scans supplement visual and keyboard checks; they do not
constitute a full accessibility certification. Reduced-motion mode disables
smooth scrolling. The screenshot checks cover persisted partial warnings,
failed refresh with retained accounts, empty and loading records, and
unavailable record/quote data. Component tests also cover activity pagination,
unavailable detail identity, precise decimal sorting, and retrying a failed
search with the same input.

Independent standards and spec reviews reported no major remaining findings.
Review corrections included preserving provider/time context, warnings after
reload, honest stale status, readable code, the specified palette and a single
type family, and fixing mobile grid overflow.

## Scope and follow-up

The unified overview aggregation API has not been implemented (#4). Account
values remain visible, and the UI explicitly states that a combined total and
allocation are unavailable. #28 tracks integration of that authoritative
overview into this visual direction. It is not replaced by a client-side sum.

Quotes still use the fake provider; #16 covers live market data. Settings,
capabilities and orders await #7, #8 and #10–#15. No inactive navigation links
are shown for those workflows. #24 remains open for the overview follow-up.

## Reproduce locally

From the branch checkout, install the locked backend/frontend dependencies.
Start the API with `PORTFOLIO_PROVIDER=fixture` and an isolated SQLite URL, then
start the Vite dashboard. Refresh the portfolio and exercise the interactions
above at 1440px and 375px. Do not load a brokerage `.env` when recapturing demo
media. The default fixture feed has six activities; component tests supply
additional pages to verify pagination.
