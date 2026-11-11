import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { getIntraday, getQuote, searchInstruments, type Account } from "../../api/client";
import { IntradayChart } from "./IntradayChart";
import { TickerAccounts } from "./TickerAccounts";
import { quoteSourceLabel } from "../../lib/portfolioDisplay";
import { readErrorMessage, retryProviderRead } from "../../lib/providerErrors";

export function MarketDataSection({ accounts = [] }: { accounts?: Account[] }) {
  const [extendedHours, setExtendedHours] = useState(false);
  const [instrumentQuery, setInstrumentQuery] = useState(""),
    [submittedQuery, setSubmittedQuery] = useState<string | null>(null),
    [selectedInstrumentId, setSelectedInstrumentId] = useState<string | null>(
      new URLSearchParams(window.location.search).get("ticker"),
    );
  const search = useQuery({
    queryKey: ["instrument-search", submittedQuery],
    queryFn: () => searchInstruments(submittedQuery ?? ""),
    enabled: submittedQuery !== null,
    retry: retryProviderRead,
  });
  const quote = useQuery({
    queryKey: ["quote", selectedInstrumentId],
    queryFn: () => getQuote(selectedInstrumentId ?? ""),
    enabled: selectedInstrumentId !== null,
    refetchInterval: 15_000,
    retry: retryProviderRead,
  });
  const intraday = useQuery({
    queryKey: ["intraday", selectedInstrumentId, extendedHours],
    queryFn: () => getIntraday(selectedInstrumentId ?? "", extendedHours),
    enabled: selectedInstrumentId !== null,
    refetchInterval: 60_000,
  });
  function selectInstrument(id: string) {
    const url = new URL(window.location.href);
    url.searchParams.set("ticker", id);
    window.history.replaceState(null, "", url);
    setSelectedInstrumentId(id);
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSelectedInstrumentId(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("ticker");
    window.history.replaceState(null, "", url);
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
        <p className="state state-error" role="alert">
          {readErrorMessage(search.error, "Instrument search is unavailable. Try the search again.")}
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
                  selectInstrument(i.id);
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
      <div className="ticker-market-grid">
      {selectedInstrumentId && <div className="ticker-chart-panel">
        <h3>{quote.data?.quote?.instrument.symbol ?? "Selected ticker"} · Today</h3>
        <label className="ticker-session"><input type="checkbox" checked={extendedHours} onChange={(event) => setExtendedHours(event.target.checked)} />Include extended hours</label>
        {intraday.isPending && <p>Loading current-day chart…</p>}
        {intraday.isError && <p className="state state-error">Intraday chart is unavailable. <button onClick={() => void intraday.refetch()}>Retry chart</button></p>}
        {intraday.isError && intraday.data && <p className="state-error">Stale previously loaded chart. Refresh failed.</p>}
        {intraday.data && <IntradayChart history={intraday.data.history} />}
      </div>}
      {quote.isPending && selectedInstrumentId !== null && (
        <p className="state">Loading quote…</p>
      )}
      {quote.isError && (
        <p className="state state-error" role="alert">
          {readErrorMessage(quote.error, "Quote is unavailable. Select an instrument again to retry.")}
        </p>
      )}
      {quote.data?.quote && (
        <article className="quote-card" aria-label="Instrument quote">
          <div>
            <h3>{quote.data.quote.instrument.symbol} quote</h3>
            {quote.isError && <p className="state-error">Stale previously loaded quote. Refresh failed.</p>}
            <p>
              Source: {quoteSourceLabel(quote.data.quote.source)}{quote.data.quote.delay_seconds ? " · SIP, 15-minute delay" : ""} · Observed:{" "}
              {new Date(quote.data.quote.observed_at).toLocaleString()}
            </p>
          </div>
          {quote.data.quote.trade_observed_at && <p className="muted">Last trade observed: {new Date(quote.data.quote.trade_observed_at).toLocaleString()}</p>}
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
          <button type="button" onClick={() => void quote.refetch()}>Refresh quote</button>
          <small>Canonical identity: {quote.data.quote.instrument.id}</small>
        </article>
      )}
      </div>
      {quote.data?.quote && <TickerAccounts accounts={accounts} symbol={quote.data.quote.instrument.symbol} />}
    </section>
  );
}
