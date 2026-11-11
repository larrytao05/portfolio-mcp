# Market data

## Sub-features

- `instrument-search`: symbol/name search, selection and empty results.
- `instrument-quote`: identity, human-readable source, timestamp and prices.
- `quote-failure`: unavailable prices, source and retry behavior.

## How to get to it (user POV)

Click **Market data** in **Primary navigation**. Fill **Search instruments**, click **Search**, and select a returned instrument button.

## Driving it with browser automation

1. Search `VTI` and select its returned symbol/name button. In **Instrument quote**, verify `VTI`, canonical identity, **Fixture market data**, observation time, last/bid/ask prices and currency match the real fixture API response.
2. Search a nonexistent instrument and confirm **No instruments found.** Search `VTI` again and confirm recovery.
3. Search `FIXTURE_UNAVAILABLE` and select it. Confirm each absent price says **unavailable**, not zero, and identity/source/time remain visible.
4. If source labeling or errors changed, use labeled fixture response injection or the existing component tests for unknown/blank sources and a failed search followed by retry with identical input. Confirm no internal provider token is shown to the user.
5. Capture the search action and resulting quote card. Inspect focus and narrow-screen input/result wrapping.

## Gotchas

- Fixture quotes are intentionally fictional. Never switch to Schwab or load credentials when a fixture result differs from real markets.
- A successful HTTP status is insufficient: confirm the selected instrument and actual price/unavailable fields.
- Source labels differ by surface: quote-card fixture source is **Fixture market data**. Unknown sources must be labeled explicitly.

## Ticker workspace

Select VTI or NVDA after refreshing the fixture portfolio. Confirm the current-day
candle/volume chart, keyboard snapshot slider, extended-hours toggle, and quote
card render independently. Chart fixtures are explicitly fictional and are not
evidence of live Alpaca access. Confirm the selected ticker remains after reload.
Switch **Orders** and **Your positions**, then **Open Orders** and **History**, select each account, and
confirm ownership labels and exact quantities. In fixture Open orders, Schwab
shows a partially filled order and Fidelity explicitly reports unavailable open
orders. In Order history, Schwab includes a canceled order and more than 20 VTI
rows. Use Next/Previous, confirm page 2, then switch view/account and confirm the
page resets. Fidelity reports executed-only coverage. Coverage warnings must
remain visible even when no rows are returned. Inspect source, requested window,
unknown sync time and truncation warnings where fixtures/tests supply them.
Failed account requests must show an error, not an empty successful history;
retained data after failure is stale. If the documented browser driver cannot
inject a failed response, mark that browser journey BLOCKED and cite the
component/API test separately.
Check chart/quote alignment on desktop and single-column stacking on mobile.
Normal startup defaults to live providers, but this verifier explicitly selects
fixtures. Never load credentials for this browser proof.
