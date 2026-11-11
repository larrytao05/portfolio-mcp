import { useState } from "react";
import type { IntradayHistory } from "../../api/client";

function marketTime(value: string) {
  return new Date(value).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" });
}

export function IntradayChart({ history }: { history: IntradayHistory }) {
  const [inspection, setInspection] = useState<number | null>(null);
  const bars = history.bars;
  if (!bars.length) return <p className="state">No recorded trades for this session yet. Data is delayed by 15 minutes.</p>;
  const selectedIndex = Math.min(inspection ?? bars.length - 1, bars.length - 1);
  const selected = bars[selectedIndex];
  const low = Math.min(...bars.map((bar) => Number(bar.low)));
  const high = Math.max(...bars.map((bar) => Number(bar.high)));
  const spread = high - low || 1;
  const maxVolume = Math.max(...bars.map((bar) => Number(bar.volume)), 1);
  const x = (index: number) => 30 + index * 740 / Math.max(bars.length - 1, 1);
  const y = (value: string) => 25 + (high - Number(value)) / spread * 200;
  const width = Math.min(8, 600 / bars.length);
  return <div className="intraday-chart">
    <p aria-live="polite" className="chart-readout">{marketTime(selected.time)} ET · O {selected.open} · H {selected.high} · L {selected.low} · C {selected.close} · Volume {selected.volume}</p>
    <svg viewBox="0 0 800 310" role="img" aria-label="Current-day price candles and volume">
      <text x="0" y="15">{high.toFixed(2)}</text><text x="0" y="245">{low.toFixed(2)}</text>
      {bars.map((bar, index) => {
        const color = Number(bar.close) >= Number(bar.open) ? "#16785c" : "#c44a57";
        return <g key={bar.time} onMouseEnter={() => setInspection(index)}>
          <line x1={x(index)} x2={x(index)} y1={y(bar.high)} y2={y(bar.low)} stroke={color} />
          <rect x={x(index) - width / 2} y={Math.min(y(bar.open), y(bar.close))} width={width} height={Math.max(Math.abs(y(bar.open) - y(bar.close)), 1)} fill={color} />
          <rect x={x(index) - width / 2} y={295 - Number(bar.volume) / maxVolume * 40} width={width} height={Number(bar.volume) / maxVolume * 40} fill={color} opacity=".4" />
          <rect x={x(index) - 740 / bars.length / 2} y="20" width={Math.max(740 / bars.length, 1)} height="280" fill="transparent" />
        </g>;
      })}
      <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1="20" y2="300" stroke="#888" strokeDasharray="3 4" />
    </svg>
    <div className="chart-dates"><span>{marketTime(bars[0].time)} ET</span><span>{marketTime(bars[bars.length - 1].time)} ET</span></div>
    <label className="snapshot-control">Inspect price candle<input aria-label="Inspect price candle" type="range" min="0" max={bars.length - 1} value={selectedIndex} onChange={(event) => setInspection(Number(event.target.value))} /></label>
    <p className="muted">One-minute candles · {history.session_date} · {history.extended_hours ? "Extended hours" : "Regular session"} · {history.feed === "fixture" ? "Fictional fixture data" : "SIP, 15-minute delay"}. Available through {marketTime(history.available_through)} ET.</p>
  </div>;
}
