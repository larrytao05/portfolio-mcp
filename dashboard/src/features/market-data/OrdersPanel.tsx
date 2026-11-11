import { useState } from "react";
import { ApiError, cancelBrokerOrder, reconcileCancellation } from "../../api/client";

export type DisplayOrder = {
  id: string;
  account_id: string;
  leg_id: string | null;
  client_order_id: string | null;
  symbol: string;
  side: string | null;
  order_type: string | null;
  quantity: string | null;
  filled_quantity: string | null;
  filled_avg_price: string | null;
  limit_price: string | null;
  status: string;
  time_placed: string | null;
  time_executed: string | null;
};

export type DisplayOrderHistory = {
  coverage: "open_and_executed" | "executed_only" | "unsupported";
  source: string;
  freshness: string;
  warning: string;
  synced_at: string | null;
  lookback_days: number | null;
  truncated: boolean;
  orders: DisplayOrder[];
};

export function OrdersPanel({ accountLabel, symbol, view, history, failed }: {
  accountLabel: string;
  symbol: string;
  view: "open" | "closed";
  history: DisplayOrderHistory;
  failed: boolean;
}) {
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<DisplayOrder | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  async function cancel() {
    if (!selected || pending) return;
    setPending(true);
    try { setResult((await cancelBrokerOrder(selected.account_id, selected.id)).state); }
    catch (error) { setResult(error instanceof ApiError && error.status >= 400 && error.status < 500 ? "NOT_CONFIRMED" : "UNKNOWN"); }
    finally { setPending(false); }
  }
  async function reconcile() {
    if (!selected || pending) return;
    setPending(true);
    try { setResult((await reconcileCancellation(selected.account_id, selected.id)).state); }
    catch { setResult("UNKNOWN"); }
    finally { setPending(false); }
  }

  const pageSize = 20;
  const lastPage = Math.max(0, Math.ceil(history.orders.length / pageSize) - 1);
  const currentPage = Math.min(page, lastPage);
  const unavailable = history.coverage === "unsupported" || (view === "open" && history.coverage === "executed_only");
  const visible = unavailable ? [] : history.orders.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  return <>
    {failed && <p className="state-error">Refresh failed. Previously loaded orders are stale.</p>}
    <p className="muted">{history.warning} Source: {history.source}. Freshness: {history.freshness}. {history.synced_at ? `Synced ${new Date(history.synced_at).toLocaleString()}.` : "Source sync time unavailable."} {history.lookback_days !== null && `Requested window: ${history.lookback_days} days.`}</p>
    {history.truncated && <p className="state-error">The provider returned its result limit. More orders may be missing.</p>}
    {unavailable && <p>{view === "open" && history.coverage === "executed_only" ? "Open orders are unavailable for this connection." : "Order coverage is unavailable for this connection."}</p>}
    {!unavailable && !history.orders.length && <p>No {view === "open" ? "open" : "historical"} {symbol} orders returned in the available coverage.</p>}
    {!!visible.length && <div className="table-scroll" role="region" aria-label={`${view === "open" ? "Open orders" : "Order history"} for ${accountLabel}`} tabIndex={0}><table><thead><tr><th>Symbol</th><th>Side</th><th>Type</th><th>Quantity</th><th>Filled</th><th>Average fill</th><th>Limit price</th><th>Status</th><th>Submitted</th><th>Broker order ID</th><th>Client order ID</th><th>Action</th></tr></thead><tbody>{visible.map((order, index) => <tr key={`${order.account_id}-${order.id}-${order.leg_id ?? ""}-${index}`}><td>{order.symbol}</td><td>{order.side ?? "Unavailable"}</td><td>{order.order_type ?? "Unavailable"}</td><td>{order.quantity ?? "Unavailable"}</td><td>{order.filled_quantity ?? "Unavailable"}</td><td>{order.filled_avg_price ?? "Unavailable"}</td><td>{order.limit_price ?? "Unavailable"}</td><td>{order.status}</td><td>{order.time_placed ? new Date(order.time_placed).toLocaleString() : "Unavailable"}</td><td>{order.id}</td><td>{order.client_order_id ?? "Unavailable"}</td><td>{view === "open" && (history.source === "Alpaca" || history.source === "Fixture") && ["new", "accepted", "pending_new", "partially_filled", "working"].includes(order.status.toLowerCase()) && <button type="button" onClick={() => { setSelected(order); setResult(null); }}>Review cancellation</button>}</td></tr>)}</tbody></table></div>}
    {selected && <section aria-label="Cancellation confirmation">
      <p>Cancel {selected.side} {selected.quantity ?? "unavailable"} {selected.symbol} for {accountLabel} (account ••••{selected.account_id.slice(-4)})? Broker order {selected.id}.</p>
      {history.source === "Alpaca" && <p>This is a LIVE Alpaca order.</p>}
      {result === null && <><button type="button" disabled={pending} onClick={() => void cancel()}>{pending ? "Requesting…" : "Confirm cancellation"}</button><button type="button" disabled={pending} onClick={() => setSelected(null)}>Keep order</button></>}
      {result !== null && <p role="status">Cancellation state: {result}. {result === "UNKNOWN" || result === "CANCEL_PENDING" || result === "NOT_CONFIRMED" ? "Do not retry; check saved cancellation status before trading." : ""}</p>}
      {(result === "UNKNOWN" || result === "CANCEL_PENDING") && <button type="button" disabled={pending} onClick={() => void reconcile()}>Reconcile cancellation</button>}
    </section>}
    {!unavailable && lastPage > 0 && <div aria-label={`Order pages for ${accountLabel}`}><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button><span> Page {currentPage + 1} of {lastPage + 1} </span><button disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next</button></div>}
  </>;
}
