import { FormEvent, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getAccountPositions,
  getAccounts,
  getHealth,
  getLatestRefresh,
  getQuote,
  refreshPortfolio,
  searchInstruments,
} from "./api/client";

export function App() {
  const queryClient = useQueryClient();
  const [instrumentQuery, setInstrumentQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState<string | null>(null);
  const [selectedInstrumentId, setSelectedInstrumentId] = useState<string | null>(null);
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
  const instrumentSearch = useQuery({
    queryKey: ["instrument-search", submittedQuery],
    queryFn: () => searchInstruments(submittedQuery ?? ""),
    enabled: submittedQuery !== null,
  });
  const quote = useQuery({
    queryKey: ["quote", selectedInstrumentId],
    queryFn: () => getQuote(selectedInstrumentId ?? ""),
    enabled: selectedInstrumentId !== null,
  });

  function submitInstrumentSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSelectedInstrumentId(null);
    setSubmittedQuery(instrumentQuery);
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

      <section aria-labelledby="market-data-heading">
        <h2 id="market-data-heading">Market data</h2>
        <form onSubmit={submitInstrumentSearch}>
          <label htmlFor="instrument-search">Search instruments</label>
          <input
            id="instrument-search"
            onChange={(event) => setInstrumentQuery(event.target.value)}
            value={instrumentQuery}
          />
          <button type="submit">Search</button>
        </form>
        {instrumentSearch.isPending && <p>Searching instruments…</p>}
        {instrumentSearch.isError && <p>Instrument search is unavailable.</p>}
        {instrumentSearch.data?.instruments.length === 0 && <p>No instruments found.</p>}
        {instrumentSearch.data && instrumentSearch.data.instruments.length > 0 && (
          <ul>
            {instrumentSearch.data.instruments.map((instrument) => (
              <li key={instrument.id}>
                <button onClick={() => setSelectedInstrumentId(instrument.id)} type="button">
                  {instrument.symbol} · {instrument.name}
                </button>
                <p>Canonical identity: {instrument.id}</p>
              </li>
            ))}
          </ul>
        )}
        {quote.isPending && <p>Loading quote…</p>}
        {quote.isError && <p>Quote is unavailable.</p>}
        {quote.data?.quote && (
          <article aria-label="Instrument quote">
            <h3>{quote.data.quote.instrument.symbol} quote</h3>
            <p>Canonical identity: {quote.data.quote.instrument.id}</p>
            <p>Source: {quote.data.quote.source}</p>
            <p>Observed: {new Date(quote.data.quote.observed_at).toLocaleString()}</p>
            <p>Last price: {quote.data.quote.last_price ?? "unavailable"} {quote.data.quote.currency ?? ""}</p>
            <p>Bid: {quote.data.quote.bid_price ?? "unavailable"}</p>
            <p>Ask: {quote.data.quote.ask_price ?? "unavailable"}</p>
          </article>
        )}
      </section>
    </main>
  );
}
