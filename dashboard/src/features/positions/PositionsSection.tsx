import { useQueries } from "@tanstack/react-query";
import { useState } from "react";

import { getAccount, type Account, type Position } from "../../api/client";
import { compareDecimalValues, displayValue, isStale } from "../../lib/portfolioDisplay";

type SortField = "symbol" | "account" | "quantity" | "current_price" | "market_value" | "gain_loss";
const columns: { field: SortField; label: string }[] = [
  { field: "symbol", label: "Asset" },
  { field: "account", label: "Account" },
  { field: "quantity", label: "Quantity" },
  { field: "current_price", label: "Price" },
  { field: "market_value", label: "Market value" },
  { field: "gain_loss", label: "Unrealized P/L" },
];

export function PositionsSection({ accounts, loading, error }: {
  accounts: Account[] | undefined;
  loading: boolean;
  error: boolean;
}) {
  const [search, setSearch] = useState("");
  const [accountId, setAccountId] = useState("all");
  const [assetClass, setAssetClass] = useState("all");
  const [sort, setSort] = useState<{ field: SortField; ascending: boolean }>({ field: "symbol", ascending: true });
  const details = useQueries({ queries: (accounts ?? []).map((account) => ({
    queryKey: ["accounts", account.id], queryFn: () => getAccount(account.id),
  })) });
  const rows = details.flatMap((detail, index) => (detail.data?.account.positions ?? []).map((position: Position) => ({
    account: accounts![index], position,
    stale: detail.isError || detail.data!.account.is_stale || accounts![index].is_stale || position.is_stale ||
      isStale(detail.data!.account.source_refreshed_at ?? detail.data!.account.refreshed_at),
  })));
  const filtered = rows.filter(({ account, position }) =>
    (accountId === "all" || account.id === accountId) &&
    (assetClass === "all" || position.asset_class === assetClass) &&
    [position.symbol, position.name, account.label, account.provider].some((value) => value.toLowerCase().includes(search.trim().toLowerCase())),
  ).sort((a, b) => {
    const field = sort.field;
    const left = field === "account" ? a.account.label : a.position[field];
    const right = field === "account" ? b.account.label : b.position[field];
    if (left === null) return right === null ? 0 : 1;
    if (right === null) return -1;
    const result = field === "symbol" || field === "account" ? left.localeCompare(right) : compareDecimalValues(left, right);
    return sort.ascending ? result : -result;
  });
  const pending = loading || details.some((detail) => detail.isPending);

  return <section id="positions" className="content-section positions-section" aria-labelledby="positions-heading">
    <div className="section-heading"><div><h2 id="positions-heading">Positions</h2><p className="muted">Every holding, with the account that owns it.</p></div>
      <span className="muted">{filtered.length} positions</span></div>
    <div className="compact-controls positions-controls">
      <label>Search positions<input placeholder="Symbol, name, or account" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
      <label>Position account<select value={accountId} onChange={(event) => setAccountId(event.target.value)}>
        <option value="all">All accounts</option>{accounts?.map((account) => <option key={account.id} value={account.id}>{account.label}</option>)}
      </select></label>
      <label>Position asset class<select value={assetClass} onChange={(event) => setAssetClass(event.target.value)}>
        <option value="all">All asset classes</option>{[...new Set(rows.map(({ position }) => position.asset_class))].sort().map((value) => <option key={value} value={value}>{value}</option>)}
      </select></label>
    </div>
    {pending && <p className="state" role="status">Loading positions across accounts…</p>}
    {error && <p className="inline-alert" role="alert">The account list is unavailable. Any visible positions are previously saved data; coverage may be incomplete.</p>}
    {details.map((detail, index) => detail.isError && <p className="stale-note" role="alert" key={accounts![index].id}>
      Positions for {accounts![index].label} could not be loaded. {detail.data ? "Previously saved positions are shown as stale." : "This account is missing from the table."}
    </p>)}
    {filtered.length > 0 ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Positions across accounts">
      <table className="positions-table"><thead><tr>{columns.map(({ field, label }) => <th scope="col" key={field}
        className={field === "symbol" ? "identity-column" : field === "account" ? "account-column" : "numeric"}
        aria-sort={sort.field === field ? sort.ascending ? "ascending" : "descending" : "none"}>
        <button type="button" aria-label={`Sort positions by ${label}`} onClick={() => setSort({ field, ascending: sort.field === field ? !sort.ascending : true })}>
          {label}{sort.field === field && <span aria-hidden="true"> {sort.ascending ? "↑" : "↓"}</span>}
        </button>
      </th>)}</tr></thead><tbody>{filtered.map(({ account, position, stale }, index) => <tr key={`${account.id}:${position.symbol}:${index}`}>
        <td className="identity-column"><strong>{position.symbol}</strong><span>{position.name}</span></td>
        <td className="account-column"><strong>{account.label}</strong><span>{account.provider}{stale || error ? " · Stale saved data" : ""}</span></td>
        <td className="numeric">{position.quantity}</td><td className="numeric">{displayValue(position.current_price, position.currency)}</td>
        <td className="numeric">{displayValue(position.market_value, position.currency)}</td>
        <td className={`numeric ${position.gain_loss === null ? "" : position.gain_loss.startsWith("-") ? "negative-value" : "positive-value"}`}>{displayValue(position.gain_loss, position.currency)}</td>
      </tr>)}</tbody></table>
    </div> : !pending && !error && !details.some((detail) => detail.isError) && <p className="state state-empty">{rows.length ? "No positions match these filters." : "No saved positions yet. Refresh your portfolio to load holdings."}</p>}
  </section>;
}
