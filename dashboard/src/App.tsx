import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getAccountPositions,
  getAccounts,
  getHealth,
  getLatestRefresh,
  refreshPortfolio,
} from "./api/client";

export function App() {
  const queryClient = useQueryClient();
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
  const refresh = useMutation({
    mutationFn: refreshPortfolio,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["positions"] }),
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
          <>
            <p>
              Last saved refresh: {latestRefresh.data.refresh.status} at{" "}
              {new Date(latestRefresh.data.refresh.completed_at).toLocaleString()}.
            </p>
            {latestRefresh.data.refresh.warnings.map((warning) => (
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
        {refresh.data && (
          <>
            <p>
              Refresh {refresh.data.refresh.status} at{" "}
              {new Date(refresh.data.refresh.completed_at).toLocaleString()}. Saved{" "}
              {refresh.data.refresh.accounts_refreshed} accounts and{" "}
              {refresh.data.refresh.positions_refreshed} positions.
            </p>
            {refresh.data.refresh.warnings.map((warning) => (
              <p key={warning} role="alert">
                {warning}
              </p>
            ))}
          </>
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
                  {account.is_stale && (
                    <p role="alert">
                      Stale data from {new Date(account.source_refreshed_at).toLocaleString()}.
                    </p>
                  )}
                  {accountPositions?.isPending && <p>Loading saved positions…</p>}
                  {accountPositions?.isError && <p>Saved positions are unavailable.</p>}
                  {accountPositions?.data && (
                    <ul>
                      {accountPositions.data.positions.map((position) => (
                        <li key={position.symbol}>
                          {position.symbol} · {position.quantity} shares ·{" "}
                          {position.market_value ?? "value unavailable"} {position.currency}
                          {position.is_stale && " (stale)"}
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
    </main>
  );
}
