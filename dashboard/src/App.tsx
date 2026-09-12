import { useQuery } from "@tanstack/react-query";

import { getAccounts, getHealth } from "./api/client";

export function App() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: getAccounts });

  return (
    <main>
      <header>
        <p>Local portfolio workspace</p>
        <h1>Portfolio Dashboard</h1>
        <p>
          API status: {health.isPending ? "checking" : health.data?.status ?? "unavailable"}
        </p>
      </header>

      <section aria-labelledby="accounts-heading">
        <h2 id="accounts-heading">Accounts</h2>
        {accounts.isPending && <p>Loading accounts…</p>}
        {accounts.isError && <p>Accounts are unavailable.</p>}
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
