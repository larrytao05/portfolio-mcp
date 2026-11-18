# Positions

## Sub-features

- `positions-ownership`: holdings from all saved accounts, with separate rows for the same symbol.
- `positions-filters`: search, account and asset class work together and reset predictably.
- `positions-sorting`: exact decimal order and explicit unavailable values.
- `positions-coverage`: loading, empty, failed account, stale and retained-data states.

## How to get to it (user POV)

Click **Positions** in **Primary navigation**, or scroll below the overview chart. The table lives in the **Positions across accounts** region.

## Driving it with browser automation

1. Refresh the default fixture. Confirm nine positions and that both `VTI` rows show different owning accounts (Schwab and Fidelity), quantities, prices, native currency values and unrealized P/L or **Unavailable**.
2. Set **Position account** to each account in turn. Confirm every row belongs to that account. Reset to **All accounts**. Search **Search positions** for `VTI`, then a provider name; confirm the matching rows. Search a nonexistent symbol and confirm **No positions match these filters.** Clear the input.
3. Change **Position asset class**; combine it with account and search filters, then reset all three. Confirm counts track displayed rows and neither account's holdings disappear permanently.
4. Use **Sort positions by Quantity**, **Market value** and **Unrealized P/L** in both directions. Confirm numeric ordering, `aria-sort`, negative values, null values and original displayed precision. The component test adds quantities beyond JavaScript number precision; report it separately from the normal fixture journey.
5. Reload and confirm ownership and saved rows persist. On mobile, horizontally scroll inside **Positions across accounts** and confirm the asset identity stays readable and the page itself does not widen.
6. Inject a failure only for one fixture account detail request. Confirm its label appears in the warning and other accounts remain visible. Refetch after a successful load with an injected failure; confirm retained positions show **Stale saved data**. Do not treat a partially loaded table as complete.

## Gotchas

- Scope row assertions to **Positions across accounts**; account detail tables repeat symbols/amounts.
- Positions, quotes, quantities and P/L are server strings. Sorting may compare exact strings but must not rebuild financial aggregates.
- Initial account-list failure, detail failure without cached data, and failed refetch with cached data are distinct states. Cover all affected paths when changing fetching.
- No browser interception support means the failure-injection journey is `BLOCKED`; passing component tests is supplemental evidence.
