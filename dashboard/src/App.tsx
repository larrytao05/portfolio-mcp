import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getAccounts, getHealth, refreshPortfolio } from "./api/client";

export function App() {
  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: getAccounts });
  const refresh = useMutation({
    mutationFn: refreshPortfolio,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["accounts"] }),
  });

  return (
    <main>
      <header>
        <p>Local portfolio workspace</p>
        <h1>Portfolio Dashboard</h1>
        <p>
          API status: {health.isPending ? "checking" : health.data?.status ?? "unavailable"}
        </p>
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
            {accounts.data.accounts.map((account) => (
              <li key={account.id}>
                {account.provider} · {account.label} · {account.account_type}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
