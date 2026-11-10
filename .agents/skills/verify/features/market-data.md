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
