import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type AccountDetail,
  type Position,
  getAccount,
  getActivity,
  getAccounts,
  getHealth,
  getLatestRefresh,
  refreshPortfolio,
  type Account,
  type ActivityFilters,
} from "./api/client";

type SortField = "symbol" | "quantity" | "current_price" | "market_value" | "cost_basis" | "gain_loss";
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
  return value === null ? "Unavailable" : `${value}${currency ? ` ${currency}` : ""}`;
}

function isStale(refreshedAt: string) {
  return Date.now() - new Date(refreshedAt).getTime() > 24 * 60 * 60 * 1000;
}

function compareDecimalValues(left: string | null, right: string | null) {
  if (left === right) return 0;
  if (left === null) return 1;
  if (right === null) return -1;

  const leftNegative = left.startsWith("-");
  const rightNegative = right.startsWith("-");
  if (leftNegative !== rightNegative) return leftNegative ? -1 : 1;

  const comparison = compareUnsignedDecimals(
    leftNegative ? left.slice(1) : left,
    rightNegative ? right.slice(1) : right,
  );
  return leftNegative ? -comparison : comparison;
}

function compareUnsignedDecimals(left: string, right: string) {
  const [leftWhole = "", leftFraction = ""] = left.split(".");
  const [rightWhole = "", rightFraction = ""] = right.split(".");
  const normalizedLeftWhole = leftWhole.replace(/^0+/, "") || "0";
  const normalizedRightWhole = rightWhole.replace(/^0+/, "") || "0";
  if (normalizedLeftWhole.length !== normalizedRightWhole.length) {
    return normalizedLeftWhole.length - normalizedRightWhole.length;
  }
  if (normalizedLeftWhole !== normalizedRightWhole) {
    return normalizedLeftWhole.localeCompare(normalizedRightWhole);
  }
  return leftFraction.padEnd(Math.max(leftFraction.length, rightFraction.length), "0").localeCompare(
    rightFraction.padEnd(Math.max(leftFraction.length, rightFraction.length), "0"),
  );
}

function AccountSummary({ account }: { account: AccountDetail }) {
  return (
    <dl>
      <dt>Provider</dt>
      <dd>{account.provider}</dd>
      <dt>Account type</dt>
      <dd>{account.account_type}</dd>
      <dt>Currency</dt>
      <dd>{account.currency}</dd>
      <dt>Market value</dt>
      <dd>{displayValue(account.balances.market_value, account.balances.currency)}</dd>
      <dt>Cost basis</dt>
      <dd>{displayValue(account.balances.cost_basis, account.balances.currency)}</dd>
      <dt>Freshness</dt>
      <dd>
        {isStale(account.refreshed_at) ? "Stale saved data — " : "Saved data — "}
        {new Date(account.refreshed_at).toLocaleString()}
      </dd>
      <dt>Holdings as of</dt>
      <dd>{account.as_of ?? "Unavailable"}</dd>
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
    <table>
      <thead>
        <tr>
          {(Object.keys(sortLabels) as SortField[]).map((field) => (
            <th key={field} scope="col">
              <button
                aria-label={`Sort by ${sortLabels[field]}`}
                aria-sort={sort.field === field ? sort.direction : "none"}
                onClick={() => updateSort(field)}
                type="button"
              >
                {sortLabels[field]}
              </button>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {holdings.map((position) => (
          <tr key={position.symbol}>
            <td>
              {position.symbol} — {position.name} ({position.asset_class})
            </td>
            <td>{position.quantity}</td>
            <td>{displayValue(position.current_price, position.currency)}</td>
            <td>{displayValue(position.market_value, position.currency)}</td>
            <td>{displayValue(position.cost_basis, position.currency)}</td>
            <td>{displayValue(position.gain_loss, position.currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AccountHoldings({ account }: { account: AccountDetail }) {
  const [filter, setFilter] = useState("");
  const [assetClass, setAssetClass] = useState("all");
  const [sort, setSort] = useState<{ field: SortField; direction: SortDirection }>({
    field: "symbol",
    direction: "ascending",
  });
  const assetClasses = useMemo(
    () => [...new Set(account.positions.map((position) => position.asset_class))].sort(),
    [account.positions],
  );
  const holdings = useMemo(() => {
    const search = filter.trim().toLowerCase();
    return [...account.positions]
      .filter((position) => assetClass === "all" || position.asset_class === assetClass)
      .filter(
        (position) =>
          !search ||
          [position.symbol, position.name, position.asset_class].some((value) =>
            value.toLowerCase().includes(search),
          ),
      )
      .sort((left, right) => {
        const comparison = sort.field === "symbol"
          ? left.symbol.localeCompare(right.symbol)
          : compareDecimalValues(left[sort.field], right[sort.field]);
        return sort.direction === "ascending" ? comparison : -comparison;
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
    <article aria-labelledby={`account-${account.id}`}>
      <h3 id={`account-${account.id}`}>{account.label}</h3>
      <AccountSummary account={account} />

      <h4>Holdings</h4>
      <label>
        Filter holdings
        <input
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Symbol, name, or asset class"
          value={filter}
        />
      </label>
      <label>
        Asset class
        <select onChange={(event) => setAssetClass(event.target.value)} value={assetClass}>
          <option value="all">All asset classes</option>
          {assetClasses.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      {account.positions.length === 0 ? (
        <p>This account has no saved holdings.</p>
      ) : holdings.length === 0 ? (
        <p>No saved holdings match these filters.</p>
      ) : (
        <HoldingsTable holdings={holdings} sort={sort} updateSort={updateSort} />
      )}
    </article>
  );
}

const activityPageSize = 50;

function ActivitySection({ accounts }: { accounts: Account[] }) {
  const [filters, setFilters] = useState<ActivityFilters>({});
  const [offset, setOffset] = useState(0);
  const activity = useQuery({
    queryKey: ["activity", filters, offset],
    queryFn: () => getActivity({ ...filters, limit: activityPageSize, offset }),
  });

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters(Object.fromEntries(new FormData(event.currentTarget)) as ActivityFilters);
    setOffset(0);
  }

  const pagination = activity.data?.pagination;
  const hasNextPage = pagination !== undefined && offset + pagination.limit < pagination.total;

  return (
    <section aria-labelledby="activity-heading">
      <h2 id="activity-heading">Activity</h2>
      <form onSubmit={submitFilters}>
        <label>
          Account
          <select aria-label="Activity account" defaultValue="" name="account_id">
            <option value="">All accounts</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>{account.label}</option>
            ))}
          </select>
        </label>
        <label>Provider <input aria-label="Activity provider" defaultValue="" name="provider" /></label>
        <label>Type <input aria-label="Activity type" defaultValue="" name="type" /></label>
        <label>Symbol <input aria-label="Activity symbol" defaultValue="" name="symbol" /></label>
        <label>From <input aria-label="Activity start date" defaultValue="" name="start_date" type="date" /></label>
        <label>To <input aria-label="Activity end date" defaultValue="" name="end_date" type="date" /></label>
        <button type="submit">Apply activity filters</button>
      </form>
      {activity.isPending && <p>Loading activity…</p>}
      {activity.isError && <p>Activity is unavailable.</p>}
      {activity.data?.activities.length === 0 && <p>No activity matches these filters.</p>}
      {activity.data && activity.data.activities.length > 0 && (
        <ul>
          {activity.data.activities.map((item) => (
            <li key={item.id}>
              {item.occurred_at ? new Date(item.occurred_at).toLocaleString() : item.occurred_on}
              {" · "}{item.provider} · {item.account.label} · {item.type}
              {item.symbol ? ` · ${item.symbol}` : ""} · {item.amount} {item.currency}
              {" · Imported "}{new Date(item.imported_at).toLocaleString()}
            </li>
          ))}
        </ul>
      )}
      {pagination && pagination.total > 0 && (
        <nav aria-label="Activity pagination">
          <button disabled={offset === 0} onClick={() => setOffset(offset - pagination.limit)} type="button">
            Previous activity page
          </button>
          <span>
            Showing {offset + 1}-{Math.min(offset + pagination.limit, pagination.total)} of {pagination.total}
          </span>
          <button disabled={!hasNextPage} onClick={() => setOffset(offset + pagination.limit)} type="button">
            Next activity page
          </button>
        </nav>
      )}
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
  const accountDetails = useQueries({
    queries: (accounts.data?.accounts ?? []).map((account) => ({
      queryKey: ["accounts", account.id],
      queryFn: () => getAccount(account.id),
    })),
  });
  const refresh = useMutation({
    mutationFn: refreshPortfolio,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["refreshes"] }),
        queryClient.invalidateQueries({ queryKey: ["activity"] }),
      ]);
    },
  });
  const displayedRefresh = refresh.data?.refresh ?? latestRefresh.data?.refresh;

  return (
    <main>
      <header>
        <p>Local portfolio workspace</p>
        <h1>Portfolio Dashboard</h1>
        <p>
          API status: {health.isPending ? "checking" : health.data?.status ?? "unavailable"}
        </p>
        {displayedRefresh && (
          <>
            <p>
              {refresh.data ? "Refresh" : "Last saved refresh:"} {displayedRefresh.status} at{" "}
              {new Date(displayedRefresh.completed_at).toLocaleString()}.
              {refresh.data && (
                <>
                  {" "}Saved {displayedRefresh.accounts_refreshed} accounts and{" "}
                  {displayedRefresh.positions_refreshed} positions.
                </>
              )}
            </p>
            {displayedRefresh.warnings.map((warning) => (
              <p key={warning} role="alert">
                {warning}
              </p>
            ))}
          </>
        )}
        <button
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
          type="button"
        >
          {refresh.isPending ? "Refreshing…" : "Refresh portfolio"}
        </button>
        {refresh.isError && <p>Refresh failed. Your saved data is unchanged.</p>}
      </header>

      <section aria-labelledby="accounts-heading">
        <h2 id="accounts-heading">Accounts</h2>
        {accounts.isPending && <p>Loading accounts…</p>}
        {accounts.isError && <p>Accounts are unavailable.</p>}
        {accounts.data?.accounts.length === 0 && <p>Refresh your portfolio to load saved accounts.</p>}
        {accounts.data && (
          <ul>
            {accounts.data.accounts.map((account, index) => {
              const detail = accountDetails[index];
              return (
                <li key={account.id}>
                  {account.provider} · {account.label} · {account.account_type}
                  {account.is_stale && (
                    <p role="alert">
                      Stale data from {new Date(account.source_refreshed_at).toLocaleString()}.
                    </p>
                  )}
                  {detail?.isPending && <p>Loading saved account details…</p>}
                  {detail?.isError && <p>Saved account details are unavailable.</p>}
                  {detail?.data && <AccountHoldings account={detail.data.account} />}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <ActivitySection accounts={accounts.data?.accounts ?? []} />
    </main>
  );
}
