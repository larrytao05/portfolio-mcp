import { FormEvent, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getAccountPositions,
  getActivity,
  getAccounts,
  getHealth,
  getLatestRefresh,
  refreshPortfolio,
} from "./api/client";

export function App() {
  const queryClient = useQueryClient();
  const [activityFilters, setActivityFilters] = useState({
    account_id: "",
    provider: "",
    type: "",
    symbol: "",
    start_date: "",
    end_date: "",
  });
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: getAccounts });
  const latestRefresh = useQuery({
    queryKey: ["refreshes", "latest"],
    queryFn: getLatestRefresh,
  });
  const positions = useQueries({
    queries: (accounts.data?.accounts ?? []).map((account) => ({
      queryKey: ["positions", account.id],
      queryFn: () => getAccountPositions(account.id),
    })),
  });
  const activity = useQuery({
    queryKey: ["activity", activityFilters],
    queryFn: () => getActivity(activityFilters),
  });
  const refresh = useMutation({
    mutationFn: refreshPortfolio,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["positions"] }),
        queryClient.invalidateQueries({ queryKey: ["refreshes"] }),
        queryClient.invalidateQueries({ queryKey: ["activity"] }),
      ]);
    },
  });

  function submitActivityFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActivityFilters(Object.fromEntries(new FormData(event.currentTarget)) as typeof activityFilters);
  }

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
        {accounts.data && (
          <ul>
            {accounts.data.accounts.map((account, index) => {
              const accountPositions = positions[index];
              return (
                <li key={account.id}>
                  {account.provider} · {account.label} · {account.account_type}
                  {accountPositions?.isPending && <p>Loading saved positions…</p>}
                  {accountPositions?.isError && <p>Saved positions are unavailable.</p>}
                  {accountPositions?.data && (
                    <ul>
                      {accountPositions.data.positions.map((position) => (
                        <li key={position.symbol}>
                          {position.symbol} · {position.quantity} shares ·{" "}
                          {position.market_value ?? "value unavailable"} {position.currency}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="activity-heading">
        <h2 id="activity-heading">Activity</h2>
        <form onSubmit={submitActivityFilters}>
          <label>
            Account
            <select aria-label="Activity account" defaultValue="" name="account_id">
              <option value="">All accounts</option>
              {(accounts.data?.accounts ?? []).map((account) => (
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
      </section>
    </main>
  );
}
