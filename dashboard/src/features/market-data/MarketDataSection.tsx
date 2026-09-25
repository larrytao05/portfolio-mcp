import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { getQuote, searchInstruments } from "../../api/client";

export function MarketDataSection() {
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
