import { useMemo, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type AccountDetail,
  type Position,
  getAccount,
  getAccounts,
  getHealth,
  getLatestRefresh,
  refreshPortfolio,
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

function compareValues(left: string | null, right: string | null) {
  if (left === right) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return left.localeCompare(right, undefined, { numeric: true });
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
          : compareValues(left[sort.field], right[sort.field]);
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
            {holdings.map((position: Position) => (
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
      )}
    </article>
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
      ]);
    },
  });

  return (
    <main>
      <header>
        <p>Local portfolio workspace</p>
        <h1>Portfolio Dashboard</h1>
        <p>
          API status: {health.isPending ? "checking" : health.data?.status ?? "unavailable"}
        </p>
        {latestRefresh.data?.refresh && (
          <p>
            Last saved refresh: {latestRefresh.data.refresh.status} at{" "}
            {new Date(latestRefresh.data.refresh.completed_at).toLocaleString()}.
          </p>
        )}
        <button
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
          type="button"
        >
          {refresh.isPending ? "Refreshing…" : "Refresh portfolio"}
        </button>
        {refresh.isError && <p>Refresh failed. Your saved data is unchanged.</p>}
        {refresh.data && (
          <p>
            Refresh completed at {new Date(refresh.data.refresh.completed_at).toLocaleString()}.
            Saved {refresh.data.refresh.accounts_refreshed} accounts and {refresh.data.refresh.positions_refreshed} positions.
          </p>
        )}
      </header>

      <section aria-labelledby="accounts-heading">
        <h2 id="accounts-heading">Accounts</h2>
        {accounts.isPending && <p>Loading accounts…</p>}
        {accounts.isError && <p>Accounts are unavailable.</p>}
        {accounts.data?.accounts.length === 0 && <p>Refresh your portfolio to load saved accounts.</p>}
        {accounts.data?.accounts.map((account, index) => {
          const detail = accountDetails[index];
          return (
            <div key={account.id}>
              {detail?.isPending && <p>Loading saved account details…</p>}
              {detail?.isError && <p>Saved account details are unavailable.</p>}
              {detail?.data && <AccountHoldings account={detail.data.account} />}
            </div>
          );
        })}
      </section>
    </main>
  );
}
