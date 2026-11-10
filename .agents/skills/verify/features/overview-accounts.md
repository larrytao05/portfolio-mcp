# Overview and accounts

## Sub-features

- `overview-summary`: known USD total, cash/buying power where supplied, saved time, coverage and exclusions.
- `history-range`: All, 1W, 1M, 3M, YTD and 1Y ranges on server-recorded daily values.
- `history-inspection`: precise value/date/coverage using the snapshot slider.
- `history-gaps`: missing dates and incomplete coverage stay disconnected.
- `account-details`: balances, holdings, capability descriptions, sorting/filtering.
- `refresh-persistence`: visible refresh outcome, reload, stale/partial/failed saved state.

## How to get to it (user POV)

Open the dashboard, click **Refresh portfolio**, and use **Overview** or **Accounts** in **Primary navigation**. Expand **Portfolio breakdown & recorded history** for allocations, contributions and raw history. Accounts navigation opens **Account details**.

## Driving it with browser automation

1. On an unseeded run, verify **No accounts refreshed yet**, refresh once, and confirm **Portfolio value · Known USD** and the overview status. Compare displayed decimal strings with the real `/api/overview` response using read-only API evidence; do not sum holdings in the browser.
2. Confirm the single-day chart has a visible point and **One recorded day** explanation. On `start --history`, refresh once and inspect **Portfolio value chart in USD, across all accounts**. Select **1W** and **All**; confirm the date span/snapshot slider changes and selected range has `aria-pressed=true`. Exercise the other ranges; distinguish equal output caused by insufficient data from a passed filtering test.
3. Change **Inspect recorded snapshot** with keyboard/range input. Verify date, original exact decimal value, and account coverage change together. Inspect the known seeded gap and the partial point; verify breaks and the hollow-point coverage explanation. Hover a point when supported and compare its readout.
4. Expand **Portfolio breakdown & recorded history**. Verify the table shows gaps and the partial row as **Incomplete**. Confirm chart and table agree on recorded dates/values. This is value history, not investment return.
5. Click **Accounts**, open a fixture account, filter **Filter holdings** to `VTI`, and clear it. Exercise **Asset class**, **Sort by Quantity**, and **Sort by Gain/loss**. Confirm missing quotes/cost basis display **Unavailable**, not zero.
6. Collapse **Account details** and click **Accounts** again without changing hash. Verify it opens. Reload and confirm the saved record persists.
7. For stale/partial/failed refresh changes, use separately labeled fixture fault injection or relevant API/component tests. Verify retained data is flagged, missing coverage is visible, and retries do not silently erase prior records. For a history-endpoint failure, keep the overview successful and confirm **Portfolio history is unavailable** appears.

## Gotchas

- `--history` supplies 43 days, a two-day missing interval, and one partial-coverage point; it does not supply a full year or year boundary. A meaningful YTD/year-boundary check needs a dated fixture or the chart component tests.
- Date ranges end at the latest snapshot. Refresh records daily values; repeated same-day refreshes do not create intraday data.
- Overview and account details have separate requests. A failed detail must preserve account identity and show an unavailable/stale state.
- Incomplete/stale/non-USD data must not be presented as complete combined USD totals. Preserve server totals and exclusion messages.
