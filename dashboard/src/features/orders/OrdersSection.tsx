import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import {
  type Account,
  type OrderListFilters,
  type StoredOrder,
  getOrderAudit,
  getOrders,
  refreshOrder,
} from "../../api/client";

export function OrdersSection({ accounts }: { accounts: Account[] }) {
  const queryClient = useQueryClient();
  const [hash, setHash] = useState(window.location.hash || "#overview");
  const [visibility, setVisibility] = useState(
    typeof document !== "undefined" ? document.visibilityState : "visible",
  );
  const [filters, setFilters] = useState<OrderListFilters>({});
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([
    undefined,
  ]);
  const cursor = cursorHistory[cursorHistory.length - 1];
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  const [auditCursor, setAuditCursor] = useState<string | undefined>(undefined);
  const [auditCursorHistory, setAuditCursorHistory] = useState<string[]>([]);
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
    queryKey: ["orders", filters, cursor],
    queryFn: () =>
      getOrders({
        ...filters,
        cursor: cursor || undefined,
        limit: 25,
      }),
  });

  const auditQuery = useQuery({
    queryKey: ["order-audit", selectedOrderId, auditCursor],
    queryFn: () =>
      getOrderAudit({
        order_id: selectedOrderId!,
        cursor: auditCursor,
      }),
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
    const account_id = (data.get("account_id") as string) || undefined;
    const provider = (data.get("provider") as string) || undefined;
    const stateVal = (data.get("state") as string) || undefined;
    const symbol = (data.get("symbol") as string) || undefined;
    const start_date = (data.get("start_date") as string) || undefined;
    const end_date = (data.get("end_date") as string) || undefined;

    setFilters({
      account_id,
      provider,
      state: stateVal ? [stateVal] : undefined,
      symbol,
      start_date,
      end_date,
    });
    setCursorHistory([undefined]);
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
            defaultValue={filters.account_id ?? ""}
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
          Provider
          <input
            aria-label="Filter provider"
            defaultValue={filters.provider ?? ""}
            name="provider"
            placeholder="e.g. schwab"
            type="text"
          />
        </label>
        <label>
          State
          <select
            aria-label="Filter state"
            defaultValue={filters.state?.[0] ?? ""}
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
            defaultValue={filters.symbol ?? ""}
            name="symbol"
            placeholder="e.g. VTI"
            type="text"
          />
        </label>
        <label>
          Start date
          <input
            aria-label="Filter start date"
            defaultValue={filters.start_date ?? ""}
            name="start_date"
            type="date"
          />
        </label>
        <label>
          End date
          <input
            aria-label="Filter end date"
            defaultValue={filters.end_date ?? ""}
            name="end_date"
            type="date"
          />
        </label>
        <button type="submit">Filter</button>
      </form>

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
                    <td>
                      <div>
                        {order.created_at
                          ? new Date(order.created_at).toLocaleString()
                          : "—"}
                      </div>
                      {order.provider_updated_at && (
                        <small className="muted">
                          Broker:{" "}
                          {new Date(
                            order.provider_updated_at,
                          ).toLocaleTimeString()}
                        </small>
                      )}
                    </td>
                    <td>{order.account?.label || order.account?.id || "—"}</td>
                    <td>
                      <strong>{order.instrument?.symbol || "—"}</strong>
                    </td>
                    <td>
                      <span className="badge">
                        {order.instruction.side
                          ? order.instruction.side.toUpperCase()
                          : "—"}{" "}
                        {order.instruction.type
                          ? order.instruction.type.toUpperCase()
                          : "—"}
                      </span>
                    </td>
                    <td className="numeric">
                      {order.instruction.quantity ?? "—"}
                      {order.remaining_quantity
                        ? ` (${order.remaining_quantity} rem)`
                        : ""}
                      {" @ "}
                      {order.instruction.limit_price
                        ? `$${order.instruction.limit_price}`
                        : "MKT"}
                    </td>
                    <td className="numeric">
                      {order.fill ? (
                        <>
                          {order.fill.quantity ?? "—"}
                          {order.fill.average_price
                            ? ` @ $${order.fill.average_price}`
                            : ""}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <span
                        className={`badge state-${order.state.toLowerCase()}`}
                      >
                        {order.state}
                      </span>
                      {order.warnings && order.warnings.length > 0 && (
                        <div>
                          {order.warnings.map((w) => (
                            <span key={w} className="badge">
                              {w}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td>
                      <small>
                        {order.reconciliation?.status ??
                          (order.result?.code || "—")}
                        {order.reconciliation?.source
                          ? ` (source: ${order.reconciliation.source})`
                          : order.result?.source
                            ? ` (source: ${order.result.source})`
                            : ""}
                        {order.reconciliation?.target_order_id === order.id &&
                        order.reconciliation?.next_refresh_at
                          ? ` (Next: ${new Date(order.reconciliation.next_refresh_at).toLocaleTimeString()})`
                          : ""}
                      </small>
                    </td>
                    <td>
                      <div className="compact-controls">
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
                          onClick={() => {
                            setSelectedOrderId(order.id);
                            setAuditCursor(undefined);
                            setAuditCursorHistory([]);
                          }}
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

      {(cursorHistory.length > 1 || ordersQuery.data?.next_cursor) && (
        <div className="pagination">
          {cursorHistory.length > 1 && (
            <button
              type="button"
              aria-label="Previous order page"
              onClick={() =>
                setCursorHistory((history) => history.slice(0, -1))
              }
            >
              ← Previous page
            </button>
          )}
          {ordersQuery.data?.next_cursor && (
            <button
              type="button"
              disabled={ordersQuery.isFetching}
              aria-label="Next order page"
              onClick={() => {
                const nextCursor = ordersQuery.data?.next_cursor;
                if (!nextCursor || nextCursor === cursor) return;
                setCursorHistory((history) =>
                  history[history.length - 1] === nextCursor
                    ? history
                    : [...history, nextCursor],
                );
              }}
            >
              Next page →
            </button>
          )}
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
                onClick={() => {
                  setSelectedOrderId(null);
                  setAuditCursor(undefined);
                  setAuditCursorHistory([]);
                }}
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
              <>
                <ul className="audit-event-list">
                  {auditQuery.data.events.map((evt) => (
                    <li key={evt.event_id} className="audit-event-item">
                      <div className="audit-event-meta">
                        <strong>{evt.type}</strong>
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
                <div className="pagination">
                  {auditCursorHistory.length > 0 && (
                    <button
                      type="button"
                      aria-label="Previous audit page"
                      onClick={() => {
                        const historyCopy = [...auditCursorHistory];
                        const prev = historyCopy.pop();
                        setAuditCursorHistory(historyCopy);
                        setAuditCursor(prev || undefined);
                      }}
                    >
                      ← Previous page
                    </button>
                  )}
                  {auditQuery.data?.next_cursor && (
                    <button
                      type="button"
                      aria-label="Next audit page"
                      onClick={() => {
                        setAuditCursorHistory((prev) => [
                          ...prev,
                          auditCursor || "",
                        ]);
                        setAuditCursor(
                          auditQuery.data?.next_cursor ?? undefined,
                        );
                      }}
                    >
                      Next page →
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
