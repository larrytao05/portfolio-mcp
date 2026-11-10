# Activity

## Sub-features

- `activity-feed`: saved transactions retain account/provider context after reload.
- `activity-filter`: account/provider/type/symbol/date filtering and no matches.
- `activity-pages`: next/previous page, range and disabled boundaries.
- `activity-failure`: explicit unavailable state and retry.

## How to get to it (user POV)

Refresh the fixture once and click **Activity** in **Primary navigation** to open the disclosure.

## Driving it with browser automation

1. Confirm the six fixture activities render. Reload and reopen Activity; confirm saved rows remain.
2. Set **Activity account**, **Activity provider**, **Activity type**, **Activity symbol**, **Activity start date**, or **Activity end date** as relevant, then click **Apply activity filters**. Compare the visible results against the actual `/api/activity` query response and pagination metadata.
3. Filter for a nonexistent symbol and confirm the explicit no-match state. Clear filters and reapply; confirm rows return.
4. With a fixture that supplies more than 50 activities, click **Next activity page**, confirm the visible range/rows change, and use **Previous activity page** to return. Verify first/last page controls disable appropriately. Mark this journey `BLOCKED` if the run has only the default six records; the pagination component test supplies its own extra page.
5. For activity error changes, inject an isolated fixture response failure, record it, and verify the unavailable message and retry path. Refresh the same default fixture again and verify activity is not duplicated.

## Gotchas

- Applying filters resets the page offset; changing inputs alone does not submit.
- A no-match result is distinct from API failure. Neither should appear as a perpetual loading state.
- Additional activity setup must use fixture data and the isolated database, never real brokerage history.
