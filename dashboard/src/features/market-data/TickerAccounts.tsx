import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { getAccount, getBrokerOrders, getUnresolvedCancellations, reconcileCancellation, type Account } from "../../api/client";
import { OrdersPanel } from "./OrdersPanel";

export function TickerAccounts({ accounts, symbol }: { accounts: Account[]; symbol: string }) {
  const [tab, setTab] = useState<"positions" | "orders">("orders");
  const [accountId, setAccountId] = useState("");
  const [orderView, setOrderView] = useState<"open" | "closed">("open");
  const selectedAccounts = accounts.filter((account) => !accountId || account.id === accountId);
  const positions = useQueries({ queries: selectedAccounts.map((account) => ({ queryKey: ["account", account.id], queryFn: () => getAccount(account.id), enabled: tab === "positions" })) });
  const orders = useQueries({ queries: selectedAccounts.map((account) => ({ queryKey: ["broker-orders", account.id, symbol, orderView], queryFn: () => getBrokerOrders(account.id, symbol, orderView), enabled: tab === "orders", refetchInterval: 60_000 })) });
  return <section aria-label="Your ticker accounts" className="ticker-accounts">
    <div className="ticker-tabs" role="group" aria-label="Ticker account view"><button aria-pressed={tab === "positions"} onClick={() => setTab("positions")}>Your positions</button><button aria-pressed={tab === "orders"} onClick={() => setTab("orders")}>Orders</button></div>
    <label>Ticker account<select aria-label="Ticker account" value={accountId} onChange={(event) => setAccountId(event.target.value)}><option value="">All accounts</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.label}</option>)}</select></label>
    {tab === "orders" && <div role="group" aria-label="Order view"><button aria-pressed={orderView === "open"} onClick={() => setOrderView("open")}>Open Orders</button><button aria-pressed={orderView === "closed"} onClick={() => setOrderView("closed")}>History</button></div>}
    {!accounts.length && <p className="state">Refresh your portfolio to load connected accounts.</p>}
    {selectedAccounts.map((account, index) => <article key={account.id}>
      <h4>{account.label}</h4>
      {tab === "positions" ? <>
        {positions[index].isPending && <p>Loading saved positions…</p>}
        {positions[index].isError && <p className="state-error">Saved positions are unavailable for this account.</p>}
        {positions[index].data && <>
          <p className="muted">Saved {new Date(positions[index].data.account.refreshed_at).toLocaleString()}{positions[index].data.account.is_stale ? " · Stale saved data" : ""}</p>
          {positions[index].data.account.positions.filter((position) => position.symbol === symbol).map((position) => <p key={position.symbol}>{position.symbol} · Quantity {position.quantity} · Value {position.market_value ?? "Unavailable"} {position.currency} · Cost basis {position.cost_basis ?? "Unavailable"}</p>)}
          {!positions[index].data.account.positions.some((position) => position.symbol === symbol) && <p>No saved position in {symbol}.</p>}
        </>}
      </> : <>
        <UnresolvedCancellations accountId={account.id} />
        {orders[index].isPending && <p>Loading brokerage orders…</p>}
        {orders[index].isError && <p className="state-error">Brokerage order history is unavailable for this account. Open-order coverage is unknown.</p>}
        {orders[index].data && <OrdersPanel key={`${account.id}-${symbol}-${orderView}`} accountLabel={account.label} symbol={symbol} view={orderView} history={orders[index].data.history} failed={orders[index].isError} />}
      </>}
    </article>)}
  </section>;
}

function UnresolvedCancellations({ accountId }: { accountId: string }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["cancellations", accountId], queryFn: () => getUnresolvedCancellations(accountId) });
  const reconcile = useMutation({ mutationFn: (id: string) => reconcileCancellation(accountId, id), onSuccess: () => client.invalidateQueries({ queryKey: ["cancellations", accountId] }) });
  if (query.isError) return <p role="alert">Cancellation status is unavailable.</p>;
  if (!query.data?.cancellations.length) return null;
  return <section aria-label="Unresolved cancellations"><p>These cancellations require reconciliation before new orders.</p>{query.data.cancellations.map((item) => <div key={item.broker_order_id}><span>Order {item.broker_order_id} · {item.state}</span> <button type="button" disabled={reconcile.isPending} onClick={() => reconcile.mutate(item.broker_order_id)}>Reconcile cancellation</button></div>)}{reconcile.isError && <p role="alert">Reconciliation is unavailable. Do not retry the cancellation.</p>}</section>;
}
