import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import {
  type Account,
  type StoredOrder,
  getOrderAudit,
  getOrders,
  refreshOrder,
} from "../../api/client";
import { OrderCancellationModal } from "./OrderCancellationModal";

export function OrdersSection({ accounts }: { accounts: Account[] }) {
  const queryClient = useQueryClient();
  const [hash, setHash] = useState(window.location.hash || "#overview");
  const [visibility, setVisibility] = useState(
    typeof document !== "undefined" ? document.visibilityState : "visible",
  );
  const [accountId, setAccountId] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  const [orderToCancel, setOrderToCancel] = useState<StoredOrder | null>(null);
  const [cancellationMessage, setCancellationMessage] = useState<string | null>(
    null,
  );
  const [lastRefreshMessage, setLastRefreshMessage] = useState<string | null>(
    null,
  );

  useEffect(() => {
    const onHash = () => setHash(window.location.hash || "#overview");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const onVisibility = () => setVisibility(document.visibilityState);
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const isViewActive = hash === "#orders" && visibility === "visible";

  const ordersQuery = useQuery({
    queryKey: ["orders", accountId, stateFilter, symbolFilter, cursor],
    queryFn: () =>
      getOrders({
        account_id: accountId || undefined,
        state: stateFilter ? [stateFilter] : undefined,
        symbol: symbolFilter || undefined,
        cursor: cursor || undefined,
        limit: 25,
      }),
  });

  const auditQuery = useQuery({
    queryKey: ["order-audit", selectedOrderId],
    queryFn: () => getOrderAudit({ order_id: selectedOrderId! }),
    enabled: selectedOrderId !== null,
  });

  const refreshMutation = useMutation({
    mutationFn: ({
      orderId,
      mode,
    }: {
      orderId: string;
      mode: "scheduled" | "manual";
    }) => refreshOrder(orderId, mode),
    onSuccess: (data) => {
      if (data.refresh.status === "throttled") {
        const at = data.refresh.next_refresh_at
          ? new Date(data.refresh.next_refresh_at).toLocaleTimeString()
          : "later";
        setLastRefreshMessage(`Refresh throttled. Next allowed at ${at}.`);
      } else if (data.refresh.status === "target_changed") {
        setLastRefreshMessage(
          "The scheduled refresh target changed. Reloaded fair order plan.",
        );
      } else {
        setLastRefreshMessage(null);
      }
      setCursor(undefined);
      void queryClient.invalidateQueries({ queryKey: ["orders"] });
      void queryClient.invalidateQueries({ queryKey: ["order-audit"] });
    },
  });

  const { refetch: refetchOrders } = ordersQuery;
  const { mutate: mutateRefresh } = refreshMutation;

  // Visibility and route gated background polling
  useEffect(() => {
    const ordersData = ordersQuery.data;
    if (!isViewActive || !ordersData) return;

    const orders = ordersData.orders;
    const allTerminal =
      orders.length > 0 &&
      orders.every((o) =>
        ["FILLED", "CANCELED", "REJECTED", "EXPIRED"].includes(o.state),
      );
    if (allTerminal) return;

    const hasSubmitting = orders.some((o) => o.state === "SUBMITTING");
    const groupsWithTarget = ordersData.refresh_groups.filter(
      (g) => g.target_order_id,
    );

    let delay: number | null = null;
    let action: (() => void) | null = null;

    if (groupsWithTarget.length > 0) {
      const primaryGroup = groupsWithTarget.reduce((earliest, g) =>
        new Date(g.next_refresh_at).getTime() <
        new Date(earliest.next_refresh_at).getTime()
          ? g
          : earliest,
      );
      const serverMs = new Date(ordersData.server_time).getTime();
      const nextMs = new Date(primaryGroup.next_refresh_at).getTime();
      const remainingMs = Math.max(0, nextMs - serverMs);
      const scheduledDelay = Math.max(1000, remainingMs);

      if (hasSubmitting && scheduledDelay > 2000) {
        delay = 2000;
        action = () => void refetchOrders();
      } else {
        delay = scheduledDelay;
        action = () => {
          if (primaryGroup.target_order_id) {
            mutateRefresh({
              orderId: primaryGroup.target_order_id,
              mode: "scheduled",
            });
          }
        };
      }
    } else if (hasSubmitting) {
      delay = 2000;
      action = () => void refetchOrders();
    }

    if (delay !== null && action !== null) {
      const timer = setTimeout(() => {
        if (
          window.location.hash === "#orders" &&
          document.visibilityState === "visible"
        ) {
          action!();
        }
      }, delay);
      return () => clearTimeout(timer);
    }
  }, [
    isViewActive,
    ordersQuery.data,
    refetchOrders,
    mutateRefresh,
  ]);

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setAccountId((data.get("account_id") as string) || "");
    setStateFilter((data.get("state") as string) || "");
    setSymbolFilter((data.get("symbol") as string) || "");
    setCursor(undefined);
    setCancellationMessage(null);
  }

  const offPageGroup = ordersQuery.data?.refresh_groups.find(
    (g) =>
      g.target_order_id &&
      !ordersQuery.data?.orders.some((o) => o.id === g.target_order_id),
  );

  return (
    <section
      className="content-section"
      id="orders"
      aria-labelledby="orders-heading"
    >
      <div className="section-heading">
        <div>
          <h2 id="orders-heading">Orders</h2>
        </div>
        <span className="muted">Saved orders and lifecycle audit</span>
      </div>

      <form className="filter-bar" onSubmit={submitFilters}>
        <label>
          Account
          <select
            aria-label="Filter account"
            defaultValue={accountId}
            name="account_id"
          >
            <option value="">All accounts</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          State
          <select
            aria-label="Filter state"
            defaultValue={stateFilter}
            name="state"
          >
            <option value="">All states</option>
            <option value="ACCEPTED">Accepted</option>
            <option value="SUBMITTING">Submitting</option>
            <option value="PARTIALLY_FILLED">Partially filled</option>
            <option value="FILLED">Filled</option>
            <option value="CANCEL_PENDING">Cancel pending</option>
            <option value="CANCELED">Canceled</option>
            <option value="REJECTED">Rejected</option>
            <option value="EXPIRED">Expired</option>
            <option value="UNKNOWN">Unknown</option>
          </select>
        </label>
        <label>
          Symbol
          <input
            aria-label="Filter symbol"
            defaultValue={symbolFilter}
            name="symbol"
            placeholder="e.g. VTI"
            type="text"
          />
        </label>
        <button type="submit">Filter</button>
      </form>

      {cancellationMessage && (
        <p className="inline-alert" role="status">
          {cancellationMessage}
        </p>
      )}
      {lastRefreshMessage && (
        <p className="inline-alert" role="status">
          {lastRefreshMessage}
        </p>
      )}

      {offPageGroup && offPageGroup.target_order_id && (
        <p className="stale-note" role="status">
          The server scheduled order {offPageGroup.target_order_id} (off-page in
          this account group) for the next refresh.
        </p>
      )}

      {ordersQuery.isPending && <p className="muted">Loading orders…</p>}
      {ordersQuery.isError && (
        <p className="inline-alert" role="alert">
          Unable to load orders.
        </p>
      )}

      {ordersQuery.data && ordersQuery.data.orders.length === 0 && (
        <p className="empty-state">No orders found.</p>
      )}

      {ordersQuery.data && ordersQuery.data.orders.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Created</th>
                <th scope="col">Account</th>
                <th scope="col">Symbol</th>
                <th scope="col">Side</th>
                <th scope="col" className="numeric">
                  Qty / Price
                </th>
                <th scope="col" className="numeric">
                  Fill / Avg
                </th>
                <th scope="col">State</th>
                <th scope="col">Reconciliation</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {ordersQuery.data.orders.map((order: StoredOrder) => {
                const isUnknown = order.state === "UNKNOWN";
                const isSyncable = [
                  "ACCEPTED",
                  "PARTIALLY_FILLED",
                  "CANCEL_PENDING",
                ].includes(order.state);
                return (
                  <tr key={order.id}>
                    <td>{new Date(order.created_at).toLocaleString()}</td>
                    <td>{order.account.label || order.account.id}</td>
                    <td>
                      <strong>{order.instrument.symbol}</strong>
                    </td>
                    <td>
                      <span className="badge">
                        {order.instruction.side.toUpperCase()} {order.instruction.type.toUpperCase()}
                      </span>
                    </td>
                    <td className="numeric">
                      {order.instruction.quantity} @{" "}
                      {order.instruction.limit_price ? `$${order.instruction.limit_price}` : "MKT"}
                    </td>
                    <td className="numeric">
                      {order.fill?.quantity ?? "—"}
                      {order.fill?.average_price
                        ? ` @ $${order.fill.average_price}`
                        : ""}
                    </td>
                    <td>
                      <span className={`badge state-${order.state.toLowerCase()}`}>
                        {order.state}
                      </span>
                    </td>
                    <td>
                      <small>
                        {order.reconciliation?.status ?? "pending"}
                        {order.reconciliation?.target_order_id === order.id &&
                        order.reconciliation?.next_refresh_at
                          ? ` (Next: ${new Date(order.reconciliation.next_refresh_at).toLocaleTimeString()})`
                          : ""}
                      </small>
                    </td>
                    <td>
                      <div className="compact-controls">
                        {order.can_cancel && (
                          <button
                            type="button"
                            onClick={() => {
                              setCancellationMessage(null);
                              setOrderToCancel(order);
                            }}
                          >
                            Cancel
                          </button>
                        )}
                        {isUnknown && (
                          <button
                            type="button"
                            disabled={refreshMutation.isPending}
                            onClick={() =>
                              refreshMutation.mutate({
                                orderId: order.id,
                                mode: "manual",
                              })
                            }
                          >
                            Reconcile
                          </button>
                        )}
                        {isSyncable && (
                          <button
                            type="button"
                            disabled={refreshMutation.isPending}
                            onClick={() =>
                              refreshMutation.mutate({
                                orderId: order.id,
                                mode: "manual",
                              })
                            }
                          >
                            Refresh
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => setSelectedOrderId(order.id)}
                        >
                          Audit
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {ordersQuery.data?.next_cursor && (
        <div className="pagination">
          <button
            type="button"
            onClick={() => setCursor(ordersQuery.data?.next_cursor ?? undefined)}
          >
            Next page →
          </button>
        </div>
      )}

      {selectedOrderId && (
        <div
          className="audit-modal"
          role="dialog"
          aria-labelledby="audit-heading"
        >
          <div className="audit-modal-content">
            <div className="section-heading">
              <h3 id="audit-heading">Order Audit: {selectedOrderId}</h3>
              <button
                type="button"
                onClick={() => setSelectedOrderId(null)}
                aria-label="Close audit"
              >
                ✕ Close audit
              </button>
            </div>
            {auditQuery.isPending && (
              <p className="muted">Loading audit events…</p>
            )}
            {auditQuery.isError && (
              <p className="inline-alert" role="alert">
                Unable to load audit history.
              </p>
            )}
            {auditQuery.data?.events && auditQuery.data.events.length === 0 && (
              <p className="empty-state">No audit events found.</p>
            )}
            {auditQuery.data?.events && auditQuery.data.events.length > 0 && (
              <ul className="audit-event-list">
                {auditQuery.data.events.map((evt) => (
                  <li key={evt.event_id} className="audit-event-item">
                    <div className="audit-event-meta">
                      <strong>{evt.event_type}</strong>
                      <span className="muted">
                        by {evt.actor} •{" "}
                        {new Date(evt.occurred_at).toLocaleString()}
                      </span>
                    </div>
                    {(evt.previous_state || evt.next_state) && (
                      <p>
                        Transition: {evt.previous_state ?? "none"} →{" "}
                        {evt.next_state ?? "none"}
                      </p>
                    )}
                    {evt.code && <p>Code: {evt.code}</p>}
                    {Object.keys(evt.details).length > 0 && (
                      <pre className="audit-details">
                        {JSON.stringify(evt.details, null, 2)}
                      </pre>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {orderToCancel && (
        <OrderCancellationModal
          order={orderToCancel}
          isOpen={true}
          onClose={() => setOrderToCancel(null)}
          onOrderUpdated={() => {
            void queryClient.invalidateQueries({ queryKey: ["orders"] });
            void queryClient.invalidateQueries({ queryKey: ["order-audit"] });
          }}
          onSuccess={(canceledOrder) => {
            setOrderToCancel(null);
            setCancellationMessage(
              `Order ${canceledOrder.id} (${canceledOrder.instrument.symbol}) was canceled.`,
            );
            void queryClient.invalidateQueries({ queryKey: ["orders"] });
            void queryClient.invalidateQueries({ queryKey: ["order-audit"] });
          }}
          onReconcileRequested={(orderId) => {
            mutateRefresh({ orderId, mode: "manual" });
          }}
        />
      )}
    </section>
  );
}
