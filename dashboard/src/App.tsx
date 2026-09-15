import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  type AccountDetail,
  type AllocationGroup,
  type DailyRecordedPoint,
  type PortfolioOverview,
  type Position,
  type RecordedHistory,
  type RefreshResult,
  getAccount,
  getActivity,
  getAccounts,
  getHealth,
  getLatestRefresh,
  getOverview,
  getOverviewHistory,
  getQuote,
  confirmOrderDraft,
  createOrderDraft,
  refreshPortfolio,
  searchInstruments,
  type Account,
  type ActivityFilters,
  type Order,
  type OrderDraft,
} from "./api/client";

type SortField =
  | "symbol"
  | "quantity"
  | "current_price"
  | "market_value"
  | "cost_basis"
  | "gain_loss";
type SortDirection = "ascending" | "descending";
const sortLabels: Record<SortField, string> = {
  symbol: "Holding",
  quantity: "Quantity",
  current_price: "Price",
  market_value: "Value",
  cost_basis: "Cost basis",
  gain_loss: "Gain/loss",
};
function displayValue(value: string | null, currency?: string) {
  if (value === null) return "Unavailable";
  const [whole, fraction] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${grouped}${fraction === undefined ? "" : `.${fraction}`}${currency ? ` ${currency}` : ""}`;
}
function accountTypeLabel(value: string) {
  if (value === "roth_ira") return "Roth IRA";
  if (value === "taxable_brokerage") return "Taxable brokerage";
  return value.replaceAll("_", " ");
}
function isStale(date: string) {
  const timestamp = new Date(date).getTime();
  return !Number.isFinite(timestamp) || Date.now() - timestamp > 86400000;
}
function compareDecimalValues(left: string | null, right: string | null) {
  if (left === right) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  const leftNegative = left.startsWith("-");
  const rightNegative = right.startsWith("-");
  if (leftNegative !== rightNegative) return leftNegative ? -1 : 1;
  const result = compareUnsignedDecimals(
    leftNegative ? left.slice(1) : left,
    rightNegative ? right.slice(1) : right,
  );
  return leftNegative ? -result : result;
}
function compareUnsignedDecimals(left: string, right: string) {
  const [leftWhole = "", leftFraction = ""] = left.split(".");
  const [rightWhole = "", rightFraction = ""] = right.split(".");
  const normalizedLeft = leftWhole.replace(/^0+/, "") || "0";
  const normalizedRight = rightWhole.replace(/^0+/, "") || "0";
  if (normalizedLeft.length !== normalizedRight.length)
    return normalizedLeft.length - normalizedRight.length;
  if (normalizedLeft !== normalizedRight)
    return normalizedLeft.localeCompare(normalizedRight);
  const precision = Math.max(leftFraction.length, rightFraction.length);
  return leftFraction
    .padEnd(precision, "0")
    .localeCompare(rightFraction.padEnd(precision, "0"));
}
function StatusPill({ status }: { status: string }) {
  return (
    <span className={`status status-${status}`}>
      <span aria-hidden="true" className="status-dot" />
      {status}
    </span>
  );
}

function RecordStrip({
  refresh,
  accounts,
  loading,
  unavailable,
}: {
  refresh: RefreshResult | null | undefined;
  accounts: Account[] | undefined;
  loading: boolean;
  unavailable: boolean;
}) {
  if (loading && !refresh)
    return (
      <div className="record-strip record-strip-empty" role="status">
        Loading saved record…
      </div>
    );
  if (unavailable)
    return (
      <div className="record-strip record-failed" role="alert">
        Saved record status is unavailable. Refresh to check again; any visible
        values are previously saved data.
      </div>
    );
  if (!refresh)
    return (
      <div className="record-strip record-strip-empty">
        <span className="record-mark">◌</span>
        <div>
          <strong>No saved record yet</strong>
          <span>Refresh to import provider data into this workspace.</span>
        </div>
      </div>
    );
  const staleAccounts = (accounts ?? []).filter(
    (account) => account.is_stale || isStale(account.source_refreshed_at),
  );
  const label =
    refresh.status === "success"
      ? staleAccounts.length || isStale(refresh.completed_at)
        ? "stale"
        : "fresh"
      : refresh.status;
  const coverage = refresh.provider_outcomes
    .map(
      (outcome) =>
        `${outcome.provider}: ${outcome.accounts_refreshed} refreshed, ${outcome.stale_accounts} stale, ${outcome.excluded_accounts} excluded`,
    )
    .join(" · ");
  return (
    <div className={`record-strip record-${label}`} role="status">
      <span className="record-mark" aria-hidden="true">
        {label === "fresh" ? "✓" : "!"}
      </span>
      <div>
        <strong>
          {label === "fresh"
            ? "Saved record is current"
            : label === "stale"
              ? "Saved record is stale"
              : `Refresh ${label}`}
        </strong>
        <span>
          Last refresh: {new Date(refresh.completed_at).toLocaleString()}
        </span>
        <span>{`Saved ${refresh.accounts_refreshed} accounts and ${refresh.positions_refreshed} positions`}</span>
        {coverage && <span>Coverage: {coverage}</span>}
        {staleAccounts.map((account) => (
          <span key={account.id}>
            {account.label}: stale saved data from{" "}
            {new Date(account.source_refreshed_at).toLocaleString()}
          </span>
        ))}
        {refresh.status === "failed" && (
          <span>Refresh failed. Your saved data is unchanged.</span>
        )}
      </div>
      <StatusPill status={label} />
    </div>
  );
}
function AccountSummary({ account }: { account: AccountDetail }) {
  return (
    <dl className="account-facts">
      <div>
        <dt>Provider</dt>
        <dd>{account.provider}</dd>
      </div>
      <div>
        <dt>Type</dt>
        <dd>{accountTypeLabel(account.account_type)}</dd>
      </div>
      <div>
        <dt>Currency</dt>
        <dd>{account.currency}</dd>
      </div>
      <div>
        <dt>Market value</dt>
        <dd className="numeric">
          {displayValue(
            account.balances.market_value,
            account.balances.currency,
          )}
        </dd>
      </div>
      <div>
        <dt>Cost basis</dt>
        <dd className="numeric">
          {displayValue(account.balances.cost_basis, account.balances.currency)}
        </dd>
      </div>
      <div>
        <dt>Saved at</dt>
        <dd>
          {account.is_stale || isStale(account.refreshed_at)
            ? "Stale saved data · "
            : "Saved data · "}
          {new Date(account.refreshed_at).toLocaleString()}
        </dd>
      </div>
      <div>
        <dt>Holdings as of</dt>
        <dd>{account.as_of ?? "Unavailable"}</dd>
      </div>
    </dl>
  );
}
function HoldingsTable({
  holdings,
  sort,
  updateSort,
}: {
  holdings: Position[];
  sort: { field: SortField; direction: SortDirection };
  updateSort: (field: SortField) => void;
}) {
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label="Scrollable holdings"
    >
      <table className="holdings-table">
        <thead>
          <tr>
            {(Object.keys(sortLabels) as SortField[]).map((field) => (
              <th
                key={field}
                aria-sort={sort.field === field ? sort.direction : "none"}
                className={field === "symbol" ? "identity-column" : "numeric"}
                scope="col"
              >
                <button
                  aria-label={`Sort by ${sortLabels[field]}`}
                  onClick={() => updateSort(field)}
                  type="button"
                >
                  {sortLabels[field]}
                  {sort.field === field && (
                    <span aria-hidden="true">
                      {" "}
                      {sort.direction === "ascending" ? "↑" : "↓"}
                    </span>
                  )}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {holdings.map((position) => (
            <tr key={position.symbol}>
              <td className="identity-column">
                <strong>{position.symbol}</strong>
                <span>
                  {position.name} · {position.asset_class}
                </span>
              </td>
              <td className="numeric">{position.quantity}</td>
              <td className="numeric">
                {displayValue(position.current_price, position.currency)}
              </td>
              <td className="numeric">
                {displayValue(position.market_value, position.currency)}
              </td>
              <td className="numeric">
                {displayValue(position.cost_basis, position.currency)}
              </td>
              <td className="numeric">
                {displayValue(position.gain_loss, position.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function AccountHoldings({ account }: { account: AccountDetail }) {
  const [filter, setFilter] = useState(""),
    [assetClass, setAssetClass] = useState("all"),
    [sort, setSort] = useState<{ field: SortField; direction: SortDirection }>({
      field: "symbol",
      direction: "ascending",
    });
  const assetClasses = useMemo(
    () => [...new Set(account.positions.map((p) => p.asset_class))].sort(),
    [account.positions],
  );
  const holdings = useMemo(() => {
    const search = filter.trim().toLowerCase();
    return [...account.positions]
      .filter((p) => assetClass === "all" || p.asset_class === assetClass)
      .filter(
        (p) =>
          !search ||
          [p.symbol, p.name, p.asset_class].some((v) =>
            v.toLowerCase().includes(search),
          ),
      )
      .sort((a, b) => {
        const c =
          sort.field === "symbol"
            ? a.symbol.localeCompare(b.symbol)
            : compareDecimalValues(a[sort.field], b[sort.field]);
        return sort.direction === "ascending" ? c : -c;
      });
  }, [account.positions, assetClass, filter, sort]);
  function updateSort(field: SortField) {
    setSort((current) => ({
      field,
      direction:
        current.field === field && current.direction === "ascending"
          ? "descending"
          : "ascending",
    }));
  }
  return (
    <details className="account-record" open>
      <summary>
        <span>
          <strong>{account.label}</strong>
          <small>
            {account.provider} · {accountTypeLabel(account.account_type)}
          </small>
        </span>
        <span className="summary-value">
          {displayValue(
            account.balances.market_value,
            account.balances.currency,
          )}{" "}
        </span>
      </summary>
      <div className="account-body">
        <AccountSummary account={account} />
        <div className="section-heading">
          <div>
            <h3>Holdings</h3>
          </div>
          <span className="muted">{account.positions.length} records</span>
        </div>
        <div className="compact-controls">
          <label>
            Filter holdings
            <input
              aria-label="Filter holdings"
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Symbol, name, or asset class"
              value={filter}
            />
          </label>
          <label>
            Asset class
            <select
              onChange={(e) => setAssetClass(e.target.value)}
              value={assetClass}
            >
              <option value="all">All asset classes</option>
              {assetClasses.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        </div>
        {account.positions.length === 0 ? (
          <p className="state state-empty">
            This account has no saved holdings.
          </p>
        ) : holdings.length === 0 ? (
          <p className="state state-empty">
            No saved holdings match these filters.
          </p>
        ) : (
          <HoldingsTable
            holdings={holdings}
            sort={sort}
            updateSort={updateSort}
          />
        )}
      </div>
    </details>
  );
}

function ActivitySection({ accounts }: { accounts: Account[] }) {
  const [filters, setFilters] = useState<ActivityFilters>({}),
    [offset, setOffset] = useState(0);
  const activity = useQuery({
    queryKey: ["activity", filters, offset],
    queryFn: () => getActivity({ ...filters, limit: 50, offset }),
  });
  const pagination = activity.data?.pagination;
  const next =
    pagination !== undefined && offset + pagination.limit < pagination.total;
  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters(
      Object.fromEntries(new FormData(event.currentTarget)) as ActivityFilters,
    );
    setOffset(0);
  }
  return (
    <section
      className="content-section"
      id="activity"
      aria-labelledby="activity-heading"
    >
      <div className="section-heading">
        <div>
          <h2 id="activity-heading">Activity</h2>
        </div>
        <span className="muted">Latest saved events</span>
      </div>
      <form className="filter-bar" onSubmit={submitFilters}>
        <label>
          Account
          <select
            aria-label="Activity account"
            defaultValue=""
            name="account_id"
          >
            <option value="">All accounts</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Provider
          <input
            aria-label="Activity provider"
            defaultValue=""
            name="provider"
          />
        </label>
        <label>
          Type
          <input aria-label="Activity type" defaultValue="" name="type" />
        </label>
        <label>
          Symbol
          <input aria-label="Activity symbol" defaultValue="" name="symbol" />
        </label>
        <label>
          From
          <input
            aria-label="Activity start date"
            defaultValue=""
            name="start_date"
            type="date"
          />
        </label>
        <label>
          To
          <input
            aria-label="Activity end date"
            defaultValue=""
            name="end_date"
            type="date"
          />
        </label>
        <button type="submit">Apply activity filters</button>
      </form>
      {activity.isPending && <p className="state">Loading activity…</p>}
      {activity.isError && (
        <p className="state state-error">
          Activity is unavailable. Try again after the next refresh.
        </p>
      )}
      {activity.data?.activities.length === 0 && (
        <p className="state state-empty">No activity matches these filters.</p>
      )}
      {activity.data && activity.data.activities.length > 0 && (
        <div className="activity-list">
          {activity.data.activities.map((item) => (
            <article className="activity-row" key={item.id}>
              <div className="activity-date">
                {item.occurred_at
                  ? new Date(item.occurred_at).toLocaleString()
                  : item.occurred_on}
              </div>
              <div>
                <strong>
                  {item.type}
                  {item.symbol ? ` · ${item.symbol}` : ""}
                </strong>
                <span>
                  {item.provider} · {item.description} · {item.account.label}
                </span>
              </div>
              <div className="numeric activity-amount">
                {displayValue(item.amount, item.currency)}
                <small>
                  Imported {new Date(item.imported_at).toLocaleString()}
                </small>
              </div>
            </article>
          ))}
        </div>
      )}
      {pagination && pagination.total > 0 && (
        <nav className="pagination" aria-label="Activity pagination">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(offset - pagination.limit)}
            type="button"
          >
            Previous activity page
          </button>
          <span>
            Showing {offset + 1}-
            {Math.min(offset + pagination.limit, pagination.total)} of{" "}
            {pagination.total}
          </span>
          <button
            disabled={!next}
            onClick={() => setOffset(offset + pagination.limit)}
            type="button"
          >
            Next activity page
          </button>
        </nav>
      )}
    </section>
  );
}

function MarketDataSection() {
  const [instrumentQuery, setInstrumentQuery] = useState(""),
    [submittedQuery, setSubmittedQuery] = useState<string | null>(null),
    [selectedInstrumentId, setSelectedInstrumentId] = useState<string | null>(
      null,
    );
  const search = useQuery({
    queryKey: ["instrument-search", submittedQuery],
    queryFn: () => searchInstruments(submittedQuery ?? ""),
    enabled: submittedQuery !== null,
  });
  const quote = useQuery({
    queryKey: ["quote", selectedInstrumentId],
    queryFn: () => getQuote(selectedInstrumentId ?? ""),
    enabled: selectedInstrumentId !== null,
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSelectedInstrumentId(null);
    if (submittedQuery === instrumentQuery) void search.refetch();
    setSubmittedQuery(instrumentQuery);
  }
  return (
    <section
      className="content-section"
      id="market-data"
      aria-labelledby="market-data-heading"
    >
      <div className="section-heading">
        <div>
          <h2 id="market-data-heading">Market data</h2>
        </div>
        <span className="muted">Search by symbol or name</span>
      </div>
      <form className="search-bar" onSubmit={submit}>
        <label htmlFor="instrument-search">Search instruments</label>
        <div>
          <input
            id="instrument-search"
            onChange={(e) => setInstrumentQuery(e.target.value)}
            placeholder="e.g. VTI or treasury"
            value={instrumentQuery}
          />
          <button type="submit">Search</button>
        </div>
      </form>
      {search.isPending && submittedQuery !== null && (
        <p className="state">Searching instruments…</p>
      )}
      {search.isError && (
        <p className="state state-error">
          Instrument search is unavailable. Try the search again.
        </p>
      )}
      {search.data?.instruments.length === 0 && (
        <p className="state state-empty">
          No instruments found. Try another symbol or name.
        </p>
      )}
      {search.data && search.data.instruments.length > 0 && (
        <ul className="instrument-results">
          {search.data.instruments.map((i) => (
            <li key={i.id}>
              <button
                onClick={() => {
                  if (selectedInstrumentId === i.id) void quote.refetch();
                  setSelectedInstrumentId(i.id);
                }}
                type="button"
              >
                <strong>{i.symbol}</strong>
                <span>
                  {i.name} · {i.asset_class}
                </span>
              </button>
              <small>Canonical identity: {i.id}</small>
            </li>
          ))}
        </ul>
      )}
      {quote.isPending && selectedInstrumentId !== null && (
        <p className="state">Loading quote…</p>
      )}
      {quote.isError && (
        <p className="state state-error">
          Quote is unavailable. Select an instrument again to retry.
        </p>
      )}
      {quote.data?.quote && (
        <article className="quote-card" aria-label="Instrument quote">
          <div>
            <h3>{quote.data.quote.instrument.symbol} quote</h3>
            <p>
              Source: {quote.data.quote.source} · Observed:{" "}
              {new Date(quote.data.quote.observed_at).toLocaleString()}
            </p>
          </div>
          <dl>
            <div>
              <dt>Last price</dt>
              <dd className="numeric">
                {quote.data.quote.last_price ?? "unavailable"}{" "}
                {quote.data.quote.currency ?? ""}
              </dd>
            </div>
            <div>
              <dt>Bid</dt>
              <dd className="numeric">
                {quote.data.quote.bid_price ?? "unavailable"}
              </dd>
            </div>
            <div>
              <dt>Ask</dt>
              <dd className="numeric">
                {quote.data.quote.ask_price ?? "unavailable"}
              </dd>
            </div>
          </dl>
          <small>Canonical identity: {quote.data.quote.instrument.id}</small>
        </article>
      )}
    </section>
  );
}


function formatPercentage(percentage: string): string {
  if (percentage.includes("%")) return percentage;
  const num = Number(percentage);
  if (Number.isNaN(num)) return percentage;
  const pct = num <= 1 && num > 0 ? num * 100 : num;
  return `${pct.toFixed(2)}%`;
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
    default:
      return reason.replace(/_/g, " ");
  }
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
                <td className="numeric">{formatPercentage(slice.percentage)}</td>
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

function OverviewSection({
  overview,
  history,
  loading,
  error,
}: {
  overview: PortfolioOverview | undefined;
  history: RecordedHistory | DailyRecordedPoint[] | undefined;
  loading: boolean;
  error: boolean;
}) {
  const points = useMemo(() => {
    const historyPoints = history
      ? Array.isArray(history)
        ? history
        : (history.points ?? [])
      : [];
    if (historyPoints.length > 0) return historyPoints;
    return overview?.history?.points ?? [];
  }, [history, overview]);

  const sortedPoints = useMemo(() => {
    return [...points].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date));
  }, [points]);

  if (loading && !overview) {
    return (
      <section className="content-section" id="overview" aria-labelledby="overview-heading">
        <div className="section-heading">
          <div>
            <h2 id="overview-heading">Portfolio overview</h2>
          </div>
          <span className="muted">Authoritative aggregate record</span>
        </div>
        <p className="state overview-loading" role="status">Loading portfolio overview…</p>
      </section>
    );
  }

  if (error && !overview) {
    return (
      <section className="content-section" id="overview" aria-labelledby="overview-heading">
        <div className="section-heading">
          <div>
            <h2 id="overview-heading">Portfolio overview</h2>
          </div>
        </div>
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
      <section className="content-section" id="overview" aria-labelledby="overview-heading">
        <div className="section-heading">
          <div>
            <h2 id="overview-heading">Portfolio overview</h2>
          </div>
          <span className="muted">Authoritative aggregate record</span>
        </div>
        <p className="state state-empty">
          No accounts refreshed yet. Refresh your portfolio to load saved accounts.
        </p>
      </section>
    );
  }

  return (
    <section className="content-section" id="overview" aria-labelledby="overview-heading">
      <div className="section-heading">
        <div>
          <h2 id="overview-heading">Portfolio overview</h2>
        </div>
        <span className="muted">Authoritative aggregate record</span>
      </div>

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
          <strong>Overview is {overview.status}</strong> · Some account data is stale or incomplete.
        </p>
      )}

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
              <span> · Saved {new Date(overview.refreshed_at).toLocaleString()}</span>
            )}
          </div>
        </div>
      </div>

      {overview.exclusions && overview.exclusions.length > 0 && (
        <div
          className="overview-exclusions"
          role="region"
          aria-label="Excluded positions and currencies"
        >
          <div className="exclusions-header">
            <span className="record-mark" aria-hidden="true">!</span>
            <div>
              <h3>Exclusions &amp; Limitations</h3>
              <p>
                The following items are excluded from total known USD value and allocations
                because they are unvalued, non-USD, or unavailable:
              </p>
            </div>
          </div>
          <ul className="exclusions-list">
            {overview.exclusions.map((exc, idx) => (
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
      )}

      <div className="overview-cards">
        <article className="overview-card" aria-label="Gain and loss summary">
          <div className="card-heading">
            <h3>Unrealized gain / loss</h3>
            <span className="muted">
              {overview.gain_loss.included_count} of{" "}
              {overview.gain_loss.included_count + overview.gain_loss.excluded_count} positions with cost basis
            </span>
          </div>
          <dl className="metric-grid">
            <div>
              <dt>Unrealized gain/loss</dt>
              <dd className="numeric">
                {displayValue(overview.gain_loss.unrealized_gain_loss, "USD")}
              </dd>
            </div>
            <div>
              <dt>Cost basis</dt>
              <dd className="numeric">
                {displayValue(overview.gain_loss.cost_basis, "USD")}
              </dd>
            </div>
            <div>
              <dt>Covered market value</dt>
              <dd className="numeric">
                {displayValue(overview.gain_loss.market_value, "USD")}
              </dd>
            </div>
            <div>
              <dt>Coverage</dt>
              <dd>
                {overview.gain_loss.included_count} included
                {overview.gain_loss.excluded_count > 0 && ` · ${overview.gain_loss.excluded_count} excluded`}
              </dd>
            </div>
          </dl>
        </article>
      </div>

      <div className="allocations-section">
        <div className="section-heading">
          <div>
            <h3>Allocation breakdown</h3>
          </div>
          <span className="muted">Server-computed distribution</span>
        </div>

        <div className="allocations-grid">
          {overview.allocations.asset_class && (
            <AllocationCard
              title="By Asset Class"
              group={overview.allocations.asset_class}
            />
          )}
          {overview.allocations.account && (
            <AllocationCard
              title="By Account"
              group={overview.allocations.account}
            />
          )}
        </div>
      </div>

      <div className="history-section">
        <div className="section-heading">
          <div>
            <h3>Recorded Value History (Not Investment Return)</h3>
          </div>
          <span className="muted">Daily snapshot timeline</span>
        </div>
        <p className="history-disclaimer muted">
          Aggregated from daily recorded account balances. Missing calendar dates are preserved
          as gaps and are never interpolated. Does not calculate investment performance or rate of return.
        </p>
        {sortedPoints.length === 0 ? (
          <p className="state state-empty">No daily snapshots recorded yet.</p>
        ) : (
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label="Recorded value history table"
          >
            <table className="history-table">
              <thead>
                <tr>
                  <th scope="col" className="identity-column">Snapshot date</th>
                  <th scope="col" className="numeric">Recorded value</th>
                  <th scope="col" className="numeric">Accounts</th>
                </tr>
              </thead>
              <tbody>
                {sortedPoints.flatMap((point, index) => {
                  const rows = [];
                  if (index > 0) {
                    const prevPoint = sortedPoints[index - 1];
                    const gapDays = dateGapDays(prevPoint.snapshot_date, point.snapshot_date);
                    if (gapDays > 1) {
                      const missingDays = gapDays - 1;
                      rows.push(
                        <tr
                          key={`gap-${prevPoint.snapshot_date}-${point.snapshot_date}`}
                          className="history-gap-row"
                        >
                          <td colSpan={3} className="history-gap-cell">
                            <span className="gap-indicator" aria-hidden="true">⋯</span>
                            <em>
                              Gap in recorded history ({missingDays} missing day{missingDays > 1 ? "s" : ""} between {prevPoint.snapshot_date} and {point.snapshot_date})
                            </em>
                          </td>
                        </tr>
                      );
                    }
                  }
                  rows.push(
                    <tr key={point.snapshot_date}>
                      <td className="identity-column">
                        <strong>{point.snapshot_date}</strong>
                      </td>
                      <td className="numeric">
                        {displayValue(point.value, point.currency)}
                      </td>
                      <td className="numeric">{point.accounts_count}</td>
                    </tr>
                  );
                  return rows;
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

function Navigation() {
  const [hash, setHash] = useState(window.location.hash || "#accounts");
  useEffect(() => {
    const update = () => setHash(window.location.hash || "#accounts");
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return (
    <nav aria-label="Primary navigation">
      {[
        ["overview", "Overview"],
        ["accounts", "Accounts"],
        ["activity", "Activity"],
        ["market-data", "Market data"],
        ["trade", "Trade"],
      ].map(([id, label]) => (
        <a
          key={id}
          href={`#${id}`}
          aria-current={hash === `#${id}` ? "location" : undefined}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}

function outcomeMessage(order: Order | null): string | null {
  if (order === null) return null;
  if (order.state === "UNKNOWN") {
    return "The outcome is unknown. Reconciliation is required; do not resubmit.";
  }
  if (order.state === "REJECTED") {
    return "Fake execution rejected the order. Review the result before creating a new draft.";
  }
  if (order.state === "ACCEPTED") {
    return "Fake execution accepted the order.";
  }
  if (order.state === "PARTIALLY_FILLED") {
    return "Fake execution partially filled the order. Monitor the remaining quantity.";
  }
  if (order.state === "FILLED") {
    return "Fake execution filled the order.";
  }
  return null;
}

function TradeSection({ accounts }: { accounts: Account[] }) {
  const [draft, setDraft] = useState<OrderDraft | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [form, setForm] = useState({
    account_id: "",
    instrument_id: "us-etf:VTI",
    side: "buy",
    order_type: "limit",
    quantity: "1",
    limit_price: "333.33",
  });
  const createDraft = useMutation({
    mutationFn: createOrderDraft,
    onSuccess: ({ draft: nextDraft }) => {
      setDraft(nextDraft);
      setOrder(null);
    },
  });
  const confirm = useMutation({
    mutationFn: () =>
      draft === null
        ? Promise.reject(new Error("No draft to confirm"))
        : confirmOrderDraft(draft.id, draft.fingerprint),
    onSuccess: ({ order: nextOrder }) => setOrder(nextOrder),
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    createDraft.mutate({
      ...form,
      limit_price: form.order_type === "limit" ? form.limit_price : null,
    });
  }
  const outcome = outcomeMessage(order);
  return (
    <section className="content-section" id="trade" aria-labelledby="trade-heading">
      <div className="section-heading">
        <div>
          <h2 id="trade-heading">Trade review</h2>
          <p className="muted">Orders use the offline fake execution provider.</p>
        </div>
      </div>
      <form className="filter-bar" onSubmit={submit}>
        <label>
          Account
          <select
            aria-label="Trade account"
            required
            value={form.account_id}
            onChange={(event) => setForm({ ...form, account_id: event.target.value })}
          >
            <option value="">Select an account</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>{account.label}</option>
            ))}
          </select>
        </label>
        <label>
          Canonical instrument ID
          <input aria-label="Canonical instrument ID" value={form.instrument_id} onChange={(event) => setForm({ ...form, instrument_id: event.target.value })} required />
        </label>
        <label>
          Side
          <select aria-label="Trade side" value={form.side} onChange={(event) => setForm({ ...form, side: event.target.value })}>
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
        </label>
        <label>
          Type
          <select aria-label="Trade type" value={form.order_type} onChange={(event) => setForm({ ...form, order_type: event.target.value })}>
            <option value="limit">Limit</option>
            <option value="market">Market</option>
          </select>
        </label>
        <label>
          Whole shares
          <input aria-label="Trade quantity" inputMode="decimal" value={form.quantity} onChange={(event) => setForm({ ...form, quantity: event.target.value })} required />
        </label>
        {form.order_type === "limit" && (
          <label>
            Limit price
            <input aria-label="Limit price" inputMode="decimal" value={form.limit_price} onChange={(event) => setForm({ ...form, limit_price: event.target.value })} required />
          </label>
        )}
        <button type="submit" disabled={createDraft.isPending || accounts.length === 0}>
          {createDraft.isPending ? "Creating draft…" : "Review fake order"}
        </button>
      </form>
      {createDraft.isError && <p className="inline-alert" role="alert">Unable to create a safe order draft.</p>}
      {draft !== null && (
        <article className="quote-card" aria-label="Order review">
          <div>
            <h3>Confirm fake order</h3>
            <p>
              Fake execution provider · {draft.account.label} · {draft.instruction.side.toUpperCase()} {draft.instruction.quantity} {draft.instruction.type.toUpperCase()} · {draft.instruction.limit_price ?? "market price"}
            </p>
            <dl>
              <div><dt>Instrument</dt><dd>{draft.instrument.name} ({draft.instrument.symbol})</dd></div>
              <div><dt>Canonical instrument ID</dt><dd>{draft.instrument.id}</dd></div>
              <div><dt>Time in force</dt><dd>{draft.instruction.time_in_force.toUpperCase()}</dd></div>
              <div><dt>Quote</dt><dd>Last {draft.quote.last_price ?? "unavailable"} · Bid {draft.quote.bid_price ?? "unavailable"} · Ask {draft.quote.ask_price ?? "unavailable"}</dd></div>
              <div><dt>Quote source</dt><dd>{draft.quote.source ?? "unavailable"}</dd></div>
              <div><dt>Quote observed</dt><dd>{draft.quote.observed_at === null ? "unavailable" : new Date(draft.quote.observed_at).toLocaleString()}</dd></div>
              <div><dt>Expires</dt><dd>{new Date(draft.expires_at).toLocaleString()}</dd></div>
              <div><dt>Fingerprint</dt><dd>{draft.fingerprint}</dd></div>
            </dl>
            {draft.warnings.length > 0 && <p>Warnings: {draft.warnings.join(", ")}</p>}
          </div>
          <button type="button" disabled={confirm.isPending || order !== null} onClick={() => confirm.mutate()}>
            {confirm.isPending ? "Confirming…" : "Confirm fake order"}
          </button>
        </article>
      )}
      {confirm.isError && <p className="inline-alert" role="alert">Confirmation was not accepted. Create a new draft if it expired or changed.</p>}
      {outcome !== null && <p className={order?.state === "UNKNOWN" ? "inline-alert" : "state"} role="status">{outcome}</p>}
    </section>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: getAccounts });
  const latestRefresh = useQuery({
    queryKey: ["refreshes", "latest"],
    queryFn: getLatestRefresh,
  });
  const overviewQuery = useQuery({
    queryKey: ["overview"],
    queryFn: getOverview,
  });
  const overviewHistoryQuery = useQuery({
    queryKey: ["overview", "history"],
    queryFn: getOverviewHistory,
  });
  const details = useQueries({
    queries: (accounts.data?.accounts ?? []).map((a) => ({
      queryKey: ["accounts", a.id],
      queryFn: () => getAccount(a.id),
    })),
  });
  const refresh = useMutation({
    mutationFn: refreshPortfolio,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["refreshes"] }),
        queryClient.invalidateQueries({ queryKey: ["activity"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ]);
    },
  });
  const shownRefresh = refresh.data?.refresh ?? latestRefresh.data?.refresh;
  return (
    <div className="app-shell">
      <aside className="side-rail">
        <a className="wordmark" href="#top">
          <span className="wordmark-mark">P</span>
          <span>
            Portfolio
            <br />
            <em>record</em>
          </span>
        </a>
        <Navigation />
        <div className="rail-footer">
          <span
            className={`connection-dot ${health.isError ? "connection-error" : health.isPending ? "connection-pending" : ""}`}
            aria-hidden="true"
          />{" "}
          API{" "}
          {health.isPending
            ? "checking"
            : health.isError
              ? "unavailable"
              : (health.data?.status ?? "unavailable")}
        </div>
      </aside>
      <main id="top" className="main-content">
        <header className="page-header">
          <div>
            <p className="kicker">Local portfolio workspace</p>
            <h1>Portfolio Dashboard</h1>
            <p className="lede">
              Saved accounts, positions, and provider observations.
            </p>
          </div>
          <button
            className="refresh-button"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
            type="button"
          >
            <span aria-hidden="true">↻</span>
            {refresh.isPending ? "Refreshing…" : "Refresh portfolio"}
          </button>
        </header>
        <RecordStrip
          refresh={shownRefresh}
          accounts={accounts.data?.accounts}
          loading={latestRefresh.isPending || accounts.isPending}
          unavailable={latestRefresh.isError || accounts.isError}
        />
        {shownRefresh?.warnings.map((w) => (
          <p
            className={
              shownRefresh.status === "failed" ? "inline-alert" : "stale-note"
            }
            key={w}
            role="alert"
          >
            {w}
          </p>
        ))}
        {refresh.isError && (
          <p className="inline-alert" role="alert">
            Refresh failed. Your saved data is unchanged.
          </p>
        )}
        <section
          className="content-section"
          id="accounts"
          aria-labelledby="accounts-heading"
        >
          <div className="section-heading">
            <div>
              <h2 id="accounts-heading">Accounts</h2>
            </div>
            <span className="muted">
              {accounts.data
                ? `${accounts.data.accounts.length} saved accounts`
                : ""}
            </span>
          </div>
          {accounts.isPending && <p className="state">Loading accounts…</p>}
          {accounts.isError && (
            <p className="state state-error">
              Accounts are unavailable. Try refreshing the portfolio.
            </p>
          )}
          {accounts.data?.accounts.length === 0 && (
            <p className="state state-empty">
              Refresh your portfolio to load saved accounts.
            </p>
          )}
          {accounts.data && accounts.data.accounts.length > 0 && (
            <div className="account-list">
              {accounts.data.accounts.map((account, index) => {
                const detail = details[index];
                return (
                  <div key={account.id}>
                    {!detail?.data && (
                      <div className="account-loading-identity">
                        <strong>{account.label}</strong>
                        <span>
                          {account.provider} ·{" "}
                          {accountTypeLabel(account.account_type)}
                        </span>
                      </div>
                    )}
                    {(account.is_stale ||
                      isStale(account.source_refreshed_at)) && (
                      <p className="stale-note" role="alert">
                        <strong>Stale saved data</strong> · Last saved{" "}
                        {new Date(account.source_refreshed_at).toLocaleString()}
                        .
                      </p>
                    )}
                    {detail?.isPending && (
                      <p className="state">Loading saved account details…</p>
                    )}
                    {detail?.isError && (
                      <p className="state state-error">
                        Saved account details are unavailable. Refresh to try
                        again.
                      </p>
                    )}
                    {detail?.data && (
                      <AccountHoldings account={detail.data.account} />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>
        <OverviewSection
          overview={overviewQuery.data?.overview}
          history={overviewHistoryQuery.data?.history ?? overviewQuery.data?.overview.history}
          loading={overviewQuery.isPending}
          error={overviewQuery.isError}
        />
        <ActivitySection accounts={accounts.data?.accounts ?? []} />
        <MarketDataSection />
        <TradeSection accounts={accounts.data?.accounts ?? []} />
      </main>
    </div>
  );
}
