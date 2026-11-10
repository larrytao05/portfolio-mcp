import { useState } from "react";

import type { DailyRecordedPoint } from "../../api/client";
import { displayValue } from "../../lib/portfolioDisplay";

const ranges = ["1W", "1M", "3M", "YTD", "1Y", "All"] as const;
const day = 86400000;

export function PortfolioChart({
  history,
  loading = false,
  error = false,
}: {
  history: DailyRecordedPoint[] | undefined;
  loading?: boolean;
  error?: boolean;
}) {
  const [range, setRange] = useState<(typeof ranges)[number]>("All");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const sorted = (history ?? []).filter((point) =>
    point.currency === "USD" && Number.isFinite(Number(point.value)) &&
    Number.isFinite(Date.parse(`${point.date}T00:00:00Z`)),
  ).sort((a, b) => a.date.localeCompare(b.date));
  const end = Date.parse(`${sorted.at(-1)?.date}T00:00:00Z`);
  const start = range === "All" ? -Infinity : range === "YTD"
    ? Date.UTC(new Date(end).getUTCFullYear(), 0, 1)
    : end - ({ "1W": 6, "1M": 29, "3M": 89, "1Y": 364 }[range]) * day;
  const points = sorted.filter((point) => Date.parse(`${point.date}T00:00:00Z`) >= start);
  const selected = points.find((point) => point.date === selectedDate) ?? points.at(-1);
  const values = points.map((point) => Number(point.value));
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || Math.max(Math.abs(max) * 0.02, 1);
  const firstDate = Date.parse(`${points[0]?.date}T00:00:00Z`);
  const timeSpan = end - firstDate || day;
  const coordinates = points.map((point) => ({
    point,
    x: points.length === 1 ? 450 : 24 + (Date.parse(`${point.date}T00:00:00Z`) - firstDate) / timeSpan * 852,
    y: max === min ? 135 : 235 - (Number(point.value) - min) / span * 200,
  }));
  const active = coordinates.find(({ point }) => point === selected);
  const hasGaps = points.some((point, i) => i > 0 &&
    Date.parse(`${point.date}T00:00:00Z`) - Date.parse(`${points[i - 1].date}T00:00:00Z`) > day);

  return (
    <div className="portfolio-chart">
      <div className="chart-toolbar">
        <h3>Portfolio value over time</h3>
        <div className="range-buttons" role="group" aria-label="Chart time range">
          {ranges.map((value) => (
            <button key={value} type="button" aria-pressed={range === value}
              onClick={() => { setRange(value); setSelectedDate(null); }}>
              {value}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="stale-note" role="alert">Portfolio history is unavailable. Any visible snapshots are previously saved data.</p>}
      {loading && points.length === 0 ? <p className="state" role="status">Loading portfolio history…</p>
        : points.length === 0 ? <p className="chart-empty">{error ? "Refresh to try loading history again." : "No daily snapshots recorded yet. Refresh your portfolio to start tracking its value."}</p>
        : <>
          <div className="chart-readout" aria-live="polite">
            <span>{selected?.date} · </span>
            <strong>{displayValue(selected?.value, "USD")}</strong>
            <span> · {selected?.accounts_count} of {selected?.accounts_total} accounts{selected?.is_complete ? "" : " · Incomplete coverage"}</span>
          </div>
          <div className="chart-plot">
            <svg viewBox="0 0 900 270" preserveAspectRatio="none" role="img" aria-label="Portfolio value chart in USD, across all accounts">
              <title>Daily recorded portfolio value</title>
              <desc>Missing dates and incomplete account coverage break the line. Use the snapshot slider to inspect exact values.</desc>
              {[35, 135, 235].map((y) => <line key={y} x1="24" x2="876" y1={y} y2={y} className="chart-grid" />)}
              {coordinates.map(({ point, x, y }, index) => {
                const previous = coordinates[index - 1];
                const connected = previous && point.is_complete && previous.point.is_complete &&
                  Date.parse(`${point.date}T00:00:00Z`) - Date.parse(`${previous.point.date}T00:00:00Z`) === day;
                return <g key={point.date}>
                  {connected && <path data-chart-segment="true" d={`M ${previous.x} ${previous.y} L ${x} ${y}`} className="chart-line" />}
                  <circle cx={x} cy={y} r={point === selected ? 5 : 3.5} vectorEffect="non-scaling-stroke"
                    className={point.is_complete ? "chart-point" : "chart-point chart-incomplete"}
                    onMouseEnter={() => setSelectedDate(point.date)}>
                    <title>{point.date}: {displayValue(point.value, point.currency)} · {point.accounts_count} of {point.accounts_total} accounts{point.is_complete ? "" : " · Incomplete"}</title>
                  </circle>
                </g>;
              })}
              {active && <line x1={active.x} x2={active.x} y1="24" y2="246" className="chart-crosshair" />}
            </svg>
            <div className={`chart-axis${min === max ? " chart-axis-flat" : ""}`} aria-hidden="true">
              <span>{displayValue(points[values.indexOf(max)]?.value)}</span>
              {min !== max && <span>{displayValue(points[values.indexOf(min)]?.value)}</span>}
            </div>
          </div>
          <div className="chart-dates" aria-hidden="true"><span>{points[0].date}</span><span>{points.at(-1)?.date}</span></div>
          <label className="snapshot-control">
            Inspect recorded snapshot
            <input type="range" min="0" max={points.length - 1} value={points.findIndex((point) => point === selected)}
              aria-valuetext={`${selected?.date}: ${displayValue(selected?.value, "USD")}, ${selected?.accounts_count} of ${selected?.accounts_total} accounts${selected?.is_complete ? "" : ", incomplete"}`}
              onChange={(event) => setSelectedDate(points[Number(event.target.value)].date)} />
          </label>
          {points.length === 1 && <p className="muted">One recorded day. Refresh on another day to build your timeline.</p>}
          {hasGaps && <p className="muted">Breaks in the line indicate missing daily snapshots.</p>}
          {points.some((point) => !point.is_complete) && <p className="muted">Hollow points have incomplete account coverage and are not joined to the line.</p>}
        </>}
      <p className="chart-caption">Daily recorded balances across all accounts · USD only. Ranges end at the latest snapshot. Deposits, withdrawals, and coverage changes affect value; this is not investment return.</p>
    </div>
  );
}
