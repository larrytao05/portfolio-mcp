import { useMemo } from "react";

import { type AllocationGroup, type DailyRecordedPoint, type GainLossCoverage, type OverviewAccountContribution, type OverviewExclusion, type PortfolioOverview } from "../../api/client";
import { StatusPill } from "../../components/StatusPill";
import { accountTypeLabel, displayValue } from "../../lib/portfolioDisplay";

function formatPercentage(
  percentage: string | null | undefined,
  display?: string | null,
): string {
  if (display) return display;
  if (!percentage) return "0.00%";
  if (percentage.endsWith("%")) return percentage;
  return `${percentage}%`;
}

function getPointDate(point: DailyRecordedPoint): string {
  return point.date ?? point.snapshot_date ?? "";
}

function parseDateUtc(dateStr: string): number {
  const [y, m, d] = dateStr.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function dateGapDays(d1: string, d2: string): number {
  const t1 = parseDateUtc(d1);
  const t2 = parseDateUtc(d2);
  const diffMs = Math.abs(t2 - t1);
  return Math.round(diffMs / 86400000);
}

function formatExclusionReason(reason: string): string {
  switch (reason) {
    case "unsupported_currency":
      return "Unsupported currency";
    case "missing_market_value":
      return "Missing market value";
    case "missing_cost_basis":
      return "Missing cost basis";
    case "provider_failed":
      return "Provider failed";
    case "stale_account":
      return "Stale account excluded";
    default:
      return reason.replace(/_/g, " ");
  }
}

function OverviewHeading() {
  return (
    <div className="section-heading">
      <div>
        <h2 id="overview-heading">Portfolio overview</h2>
      </div>
      <span className="muted">Authoritative aggregate record</span>
    </div>
  );
}

function OverviewSummary({ overview }: { overview: PortfolioOverview }) {
  const hasCash = overview.cash_usd !== undefined && overview.cash_usd !== null;
  const hasBuyingPower =
    overview.buying_power_usd !== undefined &&
    overview.buying_power_usd !== null;

  return (
    <div className="overview-strip">
      <div className="overview-total-card">
        <span className="eyebrow">Total Known USD Value</span>
        <div className="overview-headline">
          <strong className="overview-total-value">
            {displayValue(overview.total_known_usd_value, "USD")}
          </strong>
          <StatusPill status={overview.status} />
        </div>
        <div className="overview-metadata">
          <span>As of {overview.as_of ?? "Unavailable"}</span>
          {overview.refreshed_at && (
            <span>
              {" "}
              · Saved {new Date(overview.refreshed_at).toLocaleString()}
            </span>
          )}
        </div>
        {(hasCash || hasBuyingPower) && (
          <div className="overview-cash-metrics">
            {hasCash && (
              <div className="overview-metric-item">
                <span className="eyebrow">Cash</span>
                <strong className="overview-metric-value">
                  {displayValue(overview.cash_usd, "USD")}
                </strong>
              </div>
            )}
            {hasBuyingPower && (
              <div className="overview-metric-item">
                <span className="eyebrow">Buying power</span>
                <strong className="overview-metric-value">
                  {displayValue(overview.buying_power_usd, "USD")}
                </strong>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ExclusionsCallout({
  exclusions,
}: {
  exclusions: OverviewExclusion[];
}) {
  return (
    <div
      className="overview-exclusions"
      role="region"
      aria-label="Excluded positions and currencies"
    >
      <div className="exclusions-header">
        <span className="record-mark" aria-hidden="true">
          !
        </span>
        <div>
          <h3>Exclusions &amp; Limitations</h3>
          <p>
            The following items are excluded from total known USD value and
            allocations because they are unvalued, non-USD, or unavailable:
          </p>
        </div>
      </div>
      <ul className="exclusions-list">
        {exclusions.map((exc, idx) => (
          <li key={idx}>
            <strong>{exc.symbol ?? exc.account_id ?? "Provider"}</strong>
            <span className="status status-partial">
              {formatExclusionReason(exc.reason)}
            </span>
            <span>{exc.details}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function GainLossSummary({ gainLoss }: { gainLoss: GainLossCoverage }) {
  const totalPositions = gainLoss.included_count + gainLoss.excluded_count;
  return (
    <div className="overview-cards">
      <article className="overview-card" aria-label="Gain and loss summary">
        <div className="card-heading">
          <h3>Unrealized gain / loss</h3>
          <span className="muted">
            {gainLoss.included_count} of {totalPositions} positions with cost basis
          </span>
        </div>
        <dl className="metric-grid">
          <div>
            <dt>Unrealized gain/loss</dt>
            <dd className="numeric">
              {displayValue(gainLoss.unrealized_gain_loss, "USD")}
            </dd>
          </div>
          <div>
            <dt>Cost basis</dt>
            <dd className="numeric">
              {displayValue(gainLoss.cost_basis, "USD")}
            </dd>
          </div>
          <div>
            <dt>Covered market value</dt>
            <dd className="numeric">
              {displayValue(gainLoss.market_value, "USD")}
            </dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>
              {gainLoss.included_count} included
              {gainLoss.excluded_count > 0 &&
                ` · ${gainLoss.excluded_count} excluded`}
            </dd>
          </div>
        </dl>
      </article>
    </div>
  );
}

function AccountContributions({
  accounts,
}: {
  accounts: OverviewAccountContribution[];
}) {
  return (
    <div className="account-contributions-section" aria-label="Account contributions">
      <div className="section-heading">
        <div>
          <h3>Account contributions</h3>
        </div>
        <span className="muted">{accounts.length} accounts</span>
      </div>
      <div
        className="table-scroll"
        tabIndex={0}
        role="region"
        aria-label="Scrollable account contributions table"
      >
        <table className="contributions-table">
          <thead>
            <tr>
              <th scope="col" className="identity-column">
                Account
              </th>
              <th scope="col">Status</th>
              <th scope="col" className="numeric">
                Market value
              </th>
              <th scope="col" className="numeric">
                Share of total
              </th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((acc) => {
              const shareLabel =
                acc.percentage_of_total !== null
                  ? formatPercentage(
                      acc.percentage_of_total,
                      acc.percentage_of_total_display,
                    )
                  : "Excluded (Stale)";
              return (
                <tr key={acc.account_id}>
                  <td className="identity-column">
                    <strong>{acc.label}</strong>
                    <span>
                      {acc.provider} · {accountTypeLabel(acc.account_type)}
                    </span>
                  </td>
                  <td>
                    <StatusPill status={acc.is_stale ? "stale" : "fresh"} />
                  </td>
                  <td className="numeric">
                    {displayValue(acc.market_value, acc.currency)}
                  </td>
                  <td className="numeric">{shareLabel}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AllocationCard({
  title,
  group,
}: {
  title: string;
  group: AllocationGroup;
}) {
  return (
    <article className="allocation-card" aria-label={`Allocation ${title}`}>
      <div className="card-heading">
        <h4>{title}</h4>
        <span className="muted">
          Denominator: {displayValue(group.denominator, "USD")}
        </span>
      </div>
      <div
        className="table-scroll"
        tabIndex={0}
        role="region"
        aria-label={`Scrollable table for ${title}`}
      >
        <table className="allocation-table">
          <thead>
            <tr>
              <th scope="col" className="identity-column">Category</th>
              <th scope="col" className="numeric">Amount</th>
              <th scope="col" className="numeric">Percentage</th>
              <th scope="col" className="numeric">Positions</th>
            </tr>
          </thead>
          <tbody>
            {group.slices.map((slice) => (
              <tr key={slice.key}>
                <td className="identity-column">
                  <strong>{slice.label}</strong>
                </td>
                <td className="numeric">{displayValue(slice.amount, "USD")}</td>
                <td className="numeric">
                  {formatPercentage(slice.percentage, slice.percentage_display)}
                </td>
                <td className="numeric">{slice.position_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-footer">
        <small>
          {group.included_count} positions included · {group.excluded_count} excluded
        </small>
      </div>
    </article>
  );
}

function AllocationBreakdown({
  allocations,
}: {
  allocations: PortfolioOverview["allocations"];
}) {
  return (
    <div className="allocations-section">
      <div className="section-heading">
        <div>
          <h3>Allocation breakdown</h3>
        </div>
        <span className="muted">Server-computed distribution</span>
      </div>

      <div className="allocations-grid">
        {allocations.asset_class && (
          <AllocationCard
            title="By Asset Class"
            group={allocations.asset_class}
          />
        )}
        {allocations.security_type && (
          <AllocationCard
            title="By Security Type"
            group={allocations.security_type}
          />
        )}
        {allocations.account && (
          <AllocationCard
            title="By Account"
            group={allocations.account}
          />
        )}
      </div>
    </div>
  );
}

type HistoryRowItem =
  | { type: "point"; point: DailyRecordedPoint }
  | {
      type: "gap";
      key: string;
      prevDate: string;
      nextDate: string;
      missingDays: number;
    };

function deriveHistoryRows(points: DailyRecordedPoint[]): HistoryRowItem[] {
  const sorted = [...points].sort((a, b) =>
    getPointDate(a).localeCompare(getPointDate(b)),
  );
  const rows: HistoryRowItem[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const point = sorted[i];
    const pointDate = getPointDate(point);
    if (i > 0) {
      const prevDate = getPointDate(sorted[i - 1]);
      if (prevDate && pointDate) {
        const gapDays = dateGapDays(prevDate, pointDate);
        if (gapDays > 1) {
          const missingDays = gapDays - 1;
          rows.push({
            type: "gap",
            key: `gap-${prevDate}-${pointDate}`,
            prevDate,
            nextDate: pointDate,
            missingDays,
          });
        }
      }
    }
    rows.push({ type: "point", point });
  }
  return rows;
}

function HistoryGapRow({
  prevDate,
  nextDate,
  missingDays,
}: {
  prevDate: string;
  nextDate: string;
  missingDays: number;
}) {
  return (
    <tr className="history-gap-row">
      <td colSpan={3} className="history-gap-cell">
        <span className="gap-indicator" aria-hidden="true">
          ⋯
        </span>
        <em>
          Gap in recorded history ({missingDays} missing day
          {missingDays > 1 ? "s" : ""} between {prevDate} and {nextDate})
        </em>
      </td>
    </tr>
  );
}

function HistoryPointRow({ point }: { point: DailyRecordedPoint }) {
  const pointDate = getPointDate(point) || "Unavailable";
  const coverageText = point.accounts_total
    ? `${point.accounts_count} of ${point.accounts_total} accounts`
    : `${point.accounts_count} account${point.accounts_count === 1 ? "" : "s"}`;

  return (
    <tr>
      <td className="identity-column">
        <strong>{pointDate}</strong>
      </td>
      <td className="numeric">{displayValue(point.value, point.currency)}</td>
      <td className="numeric">
        <span className="history-coverage-content">
          <span>{coverageText}</span>
          {point.is_complete === false && (
            <span className="status status-partial history-incomplete-badge">
              <span aria-hidden="true" className="status-dot" />
              Incomplete
            </span>
          )}
        </span>
      </td>
    </tr>
  );
}

function RecordedHistoryTable({ points }: { points: DailyRecordedPoint[] }) {
  const rows = useMemo(() => deriveHistoryRows(points), [points]);

  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label="Recorded value history table"
    >
      <table className="history-table">
        <thead>
          <tr>
            <th scope="col" className="identity-column">
              Snapshot date
            </th>
            <th scope="col" className="numeric">
              Recorded value
            </th>
            <th scope="col" className="numeric">
              Accounts
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) =>
            item.type === "gap" ? (
              <HistoryGapRow
                key={item.key}
                prevDate={item.prevDate}
                nextDate={item.nextDate}
                missingDays={item.missingDays}
              />
            ) : (
              <HistoryPointRow
                key={getPointDate(item.point)}
                point={item.point}
              />
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}

function RecordedHistorySection({
  history,
}: {
  history: DailyRecordedPoint[] | undefined;
}) {
  const points = Array.isArray(history) ? history : [];

  return (
    <div className="history-section">
      <div className="section-heading">
        <div>
          <h3>Recorded Value History (Not Investment Return)</h3>
        </div>
        <span className="muted">Daily snapshot timeline</span>
      </div>
      <p className="history-disclaimer muted">
        Aggregated from daily recorded account balances. Missing calendar dates
        are preserved as gaps and are never interpolated. Does not calculate
        investment performance or rate of return.
      </p>
      {points.length === 0 ? (
        <p className="state state-empty">No daily snapshots recorded yet.</p>
      ) : (
        <RecordedHistoryTable points={points} />
      )}
    </div>
  );
}

export function OverviewSection({
  overview,
  history,
  loading,
  error,
}: {
  overview: PortfolioOverview | undefined;
  history: DailyRecordedPoint[] | undefined;
  loading: boolean;
  error: boolean;
}) {
  if (loading && !overview) {
    return (
      <section
        className="content-section"
        id="overview"
        aria-labelledby="overview-heading"
      >
        <OverviewHeading />
        <p className="state overview-loading" role="status">
          Loading portfolio overview…
        </p>
      </section>
    );
  }

  if (error && !overview) {
    return (
      <section
        className="content-section"
        id="overview"
        aria-labelledby="overview-heading"
      >
        <OverviewHeading />
        <p className="state state-error" role="alert">
          Portfolio overview is unavailable. Try refreshing the portfolio.
        </p>
      </section>
    );
  }

  if (
    !overview ||
    overview.status === "empty" ||
    (overview.accounts.length === 0 && overview.total_known_usd_value === null)
  ) {
    return (
      <section
        className="content-section"
        id="overview"
        aria-labelledby="overview-heading"
      >
        <OverviewHeading />
        <p className="state state-empty">
          No accounts refreshed yet. Refresh your portfolio to load saved accounts.
        </p>
      </section>
    );
  }

  return (
    <section
      className="content-section"
      id="overview"
      aria-labelledby="overview-heading"
    >
      <OverviewHeading />

      {error && (
        <p className="inline-alert" role="alert">
          Overview aggregation is unavailable. Previously saved values are shown.
        </p>
      )}

      {overview.warnings.map((w) => (
        <p className="stale-note" key={w} role="alert">
          {w}
        </p>
      ))}

      {(overview.status === "stale" || overview.status === "partial") && (
        <p className="stale-note" role="alert">
          <strong>Overview is {overview.status}</strong> · Some account data is
          stale or incomplete.
        </p>
      )}

      <OverviewSummary overview={overview} />

      {overview.exclusions && overview.exclusions.length > 0 && (
        <ExclusionsCallout exclusions={overview.exclusions} />
      )}

      <GainLossSummary gainLoss={overview.gain_loss} />

      {overview.accounts && overview.accounts.length > 0 && (
        <AccountContributions accounts={overview.accounts} />
      )}

      <AllocationBreakdown allocations={overview.allocations} />

      <RecordedHistorySection
        history={history ?? overview.history}
      />
    </section>
  );
}
