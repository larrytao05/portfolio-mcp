import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  confirmOrderDraft,
  createOrderDraft,
  getTradingSettings,
  getTradingStatus,
  searchInstruments,
  updateTradingSettings,
  type Account,
  type Order,
  type OrderDraft,
  type TradingSettings,
} from "../../api/client";
import { capabilityLabel } from "../../lib/capabilityLabel";

export function ExecutionStatus({
  status,
  loading,
  unavailable,
}: {
  status: Awaited<ReturnType<typeof getTradingStatus>> | undefined;
  loading: boolean;
  unavailable: boolean;
}) {
  return (
    <section className="content-section" id="settings" aria-labelledby="settings-heading">
      <div className="section-heading">
        <div><h2 id="settings-heading">Execution status</h2></div>
        <span className="muted">Saved capability observations</span>
      </div>
      {loading && !status && <p className="state">Loading execution status…</p>}
      {unavailable && !status && <p className="state state-error">Execution status is unavailable. Refresh to check provider status.</p>}
      {status?.accounts.length === 0 && <p className="state state-empty">No saved capability observations yet. Refresh your portfolio first.</p>}
      {status?.providers.map((provider) => (
        <div className="stale-note" key={provider.provider}>
          <p>
            <strong>{provider.provider}</strong> · Connection state: {provider.state}
          </p>
          <p>
            Last successful observation: {provider.last_success_at ? new Date(provider.last_success_at).toLocaleString() : "never"}
          </p>
          {provider.blocks.map((block) => (
            <p className="inline-alert" key={block.code} role="alert">
              {block.message}{block.recovery_action ? ` ${block.recovery_action}` : ""}
            </p>
          ))}
        </div>
      ))}
      {status?.accounts.map((capability) => (
        <article className="account-record" key={capability.account_id}>
          <div className="account-body">
            <h3>{capability.provider}</h3>
            <p>Public account ID: {capability.account_id}</p>
            <p><strong>{capabilityLabel(capability)}</strong> · Observed {capability.observed_at ? new Date(capability.observed_at).toLocaleString() : "never"}</p>
            <p>Supports: {capability.asset_classes.join(", ") || "no execution actions"} · {capability.order_types.join(", ") || "no order types"}</p>
            <p>Sides: {capability.supported_sides.join(", ") || "unavailable"} · Time in force: {capability.time_in_force.join(", ") || "unavailable"} · Sizing: {capability.sizing_modes.join(", ") || "unavailable"}</p>
            <p>Preview: {capability.preview_supported ? "available" : "unavailable"} · Cancellation: {capability.cancellation_supported ? "available" : "unavailable"}</p>
            {capability.blocks.map((block) => <p className="inline-alert" key={block.code} role="alert">{block.message}{block.recovery_action ? ` ${block.recovery_action}` : ""}</p>)}
          </div>
        </article>
      ))}
    </section>
  );
}

export function TradingSettingsPanel() {
  const settingsQuery = useQuery({
    queryKey: ["trading", "settings"],
    queryFn: getTradingSettings,
  });
  const settings = settingsQuery.data?.settings;
  const queryClient = useQueryClient();
  const [form, setForm] = useState<TradingSettings | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (settings) setForm(settings);
  }, [settings]);
  const update = useMutation({
    mutationFn: updateTradingSettings,
    onSuccess: async () => {
      setConfirmed(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["trading", "settings"] }),
        queryClient.invalidateQueries({ queryKey: ["trading", "status"] }),
      ]);
    },
  });
  if (!form) return <p className="state">Loading trading safeguards…</p>;
  const submittedForm = form;
  const requiresConfirmation =
    (!settings?.live_trading_enabled && form.live_trading_enabled) ||
    (settings?.kill_switch_active && !form.kill_switch_active);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (requiresConfirmation && !confirmed) return;
    update.mutate({
      live_trading_enabled: submittedForm.live_trading_enabled,
      kill_switch_active: submittedForm.kill_switch_active,
      max_order_shares: submittedForm.max_order_shares || null,
      max_order_notional_usd: submittedForm.max_order_notional_usd || null,
      version: submittedForm.version,
    });
  }
  return (
    <section className="content-section" aria-labelledby="safeguards-heading">
      <div className="section-heading">
        <div><h2 id="safeguards-heading">Trading safeguards</h2></div>
        <span className="muted">Dashboard-only owner controls</span>
      </div>
      <p role="status"><strong>{settings?.effective_state}</strong></p>
      <form className="filter-bar" onSubmit={submit}>
        <label>
          Maximum shares per order
          <input aria-label="Maximum shares per order" inputMode="decimal" value={form.max_order_shares ?? ""} onChange={(event) => setForm({ ...form, max_order_shares: event.target.value || null })} />
        </label>
        <label>
          Maximum USD notional per order
          <input aria-label="Maximum USD notional per order" inputMode="decimal" value={form.max_order_notional_usd ?? ""} onChange={(event) => setForm({ ...form, max_order_notional_usd: event.target.value || null })} />
        </label>
        <label>
          <input checked={form.live_trading_enabled} onChange={(event) => setForm({ ...form, live_trading_enabled: event.target.checked })} type="checkbox" />
          Enable live trading
        </label>
        <label>
          <input checked={form.kill_switch_active} onChange={(event) => setForm({ ...form, kill_switch_active: event.target.checked })} type="checkbox" />
          Kill switch active
        </label>
        {requiresConfirmation && (
          <label>
            <input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />
            I confirm this increases trading authority
          </label>
        )}
        <button disabled={update.isPending || (requiresConfirmation && !confirmed)} type="submit">Save safeguards</button>
      </form>
      {update.isError && <p className="inline-alert" role="alert">Safeguards were not changed. Reload the latest settings and try again.</p>}
    </section>
  );
}
function outcomeMessage(order: Order | null): string | null {
  if (order === null) return null;
  if (order.state === "UNKNOWN") {
    return "The outcome is unknown. Reconciliation is required; do not resubmit.";
  }
  if (order.state === "REJECTED") {
    return "Fake execution rejected the order. Review the result before creating a new draft.";
  }
  if (order.state === "ACCEPTED") {
    return "Fake execution accepted the order.";
  }
  if (order.state === "PARTIALLY_FILLED") {
    return "Fake execution partially filled the order. Monitor the remaining quantity.";
  }
  if (order.state === "FILLED") {
    return "Fake execution filled the order.";
  }
  return null;
}

export function TradeSection({ accounts }: { accounts: Account[] }) {
  const [draft, setDraft] = useState<OrderDraft | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [instrumentQuery, setInstrumentQuery] = useState("");
  const [submittedInstrumentQuery, setSubmittedInstrumentQuery] = useState<
    string | null
  >(null);
  const [form, setForm] = useState({
    account_id: "",
    instrument_id: "",
    side: "buy",
    order_type: "limit",
    quantity: "1",
    limit_price: "333.33",
  });
  const createDraft = useMutation({
    mutationFn: createOrderDraft,
    onSuccess: ({ draft: nextDraft }) => {
      setDraft(nextDraft);
      setOrder(null);
    },
  });
  const instruments = useQuery({
    queryKey: ["trade-instrument-search", submittedInstrumentQuery],
    queryFn: () => searchInstruments(submittedInstrumentQuery ?? ""),
    enabled: submittedInstrumentQuery !== null,
  });
  const confirm = useMutation({
    mutationFn: () =>
      draft === null
        ? Promise.reject(new Error("No draft to confirm"))
        : confirmOrderDraft(draft.id, draft.fingerprint),
    onSuccess: ({ order: nextOrder }) => setOrder(nextOrder),
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    createDraft.mutate({
      ...form,
      limit_price: form.order_type === "limit" ? form.limit_price : null,
    });
  }
  function search() {
    if (submittedInstrumentQuery === instrumentQuery) void instruments.refetch();
    setSubmittedInstrumentQuery(instrumentQuery);
  }
  const outcome = outcomeMessage(order);
  return (
    <section className="content-section" id="trade" aria-labelledby="trade-heading">
      <div className="section-heading">
        <div>
          <h2 id="trade-heading">Trade review</h2>
          <p className="muted">Orders use the offline fake execution provider.</p>
        </div>
      </div>
      <form className="filter-bar" onSubmit={submit}>
        <label>
          Account
          <select
            aria-label="Trade account"
            required
            value={form.account_id}
            onChange={(event) => setForm({ ...form, account_id: event.target.value })}
          >
            <option value="">Select an account</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>{account.label}</option>
            ))}
          </select>
        </label>
        <fieldset>
          <legend>Instrument</legend>
          <div className="search-bar">
            <label>
              Search trade instruments
              <input
                aria-label="Trade instrument search"
                onChange={(event) => setInstrumentQuery(event.target.value)}
                placeholder="e.g. VTI"
                value={instrumentQuery}
              />
            </label>
            <button onClick={search} type="button">Search trade instruments</button>
          </div>
          {instruments.data?.instruments.map((instrument) => (
            <button
              key={instrument.id}
              onClick={() => setForm({ ...form, instrument_id: instrument.id })}
              type="button"
            >
              Select {instrument.symbol} — {instrument.name}
            </button>
          ))}
          {form.instrument_id && <p>Selected canonical instrument: {form.instrument_id}</p>}
        </fieldset>
        <label>
          Side
          <select aria-label="Trade side" value={form.side} onChange={(event) => setForm({ ...form, side: event.target.value })}>
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
        </label>
        <label>
          Type
          <select aria-label="Trade type" value={form.order_type} onChange={(event) => setForm({ ...form, order_type: event.target.value })}>
            <option value="limit">Limit</option>
            <option value="market">Market</option>
          </select>
        </label>
        <label>
          Whole shares
          <input aria-label="Trade quantity" inputMode="decimal" value={form.quantity} onChange={(event) => setForm({ ...form, quantity: event.target.value })} required />
        </label>
        {form.order_type === "limit" && (
          <label>
            Limit price
            <input aria-label="Limit price" inputMode="decimal" value={form.limit_price} onChange={(event) => setForm({ ...form, limit_price: event.target.value })} required />
          </label>
        )}
        <button type="submit" disabled={createDraft.isPending || accounts.length === 0 || !form.instrument_id}>
          {createDraft.isPending ? "Creating draft…" : "Review fake order"}
        </button>
      </form>
      {createDraft.isError && <p className="inline-alert" role="alert">{createDraft.error.message}</p>}
      {draft !== null && (
        <article className="quote-card" aria-label="Order review">
          <div>
            <h3>Confirm fake order</h3>
            <p>
              Fake execution provider · {draft.account.label} · {draft.instruction.side.toUpperCase()} {draft.instruction.quantity} {draft.instruction.type.toUpperCase()} · {draft.instruction.limit_price ?? "market price"}
            </p>
            <dl>
              <div><dt>Instrument</dt><dd>{draft.instrument.name} ({draft.instrument.symbol})</dd></div>
              <div><dt>Canonical instrument ID</dt><dd>{draft.instrument.id}</dd></div>
              <div><dt>Time in force</dt><dd>{draft.instruction.time_in_force.toUpperCase()}</dd></div>
              <div><dt>Quote</dt><dd>Last {draft.quote.last_price ?? "unavailable"} · Bid {draft.quote.bid_price ?? "unavailable"} · Ask {draft.quote.ask_price ?? "unavailable"}</dd></div>
              <div><dt>Quote source</dt><dd>{draft.quote.source ?? "unavailable"}</dd></div>
              <div><dt>Quote observed</dt><dd>{draft.quote.observed_at === null ? "unavailable" : new Date(draft.quote.observed_at).toLocaleString()}</dd></div>
              <div><dt>Estimated notional</dt><dd>{draft.safety.estimated_notional ?? "unavailable"}</dd></div>
              <div><dt>Account refreshed</dt><dd>{draft.safety.account_refreshed_at === null ? "unavailable" : new Date(draft.safety.account_refreshed_at).toLocaleString()}</dd></div>
              <div><dt>Capability observed</dt><dd>{draft.safety.capability_observed_at === null ? "unavailable" : new Date(draft.safety.capability_observed_at).toLocaleString()}</dd></div>
              <div><dt>Capability last confirmed</dt><dd>{draft.safety.capability_last_success_at === null ? "unavailable" : new Date(draft.safety.capability_last_success_at).toLocaleString()}</dd></div>
              <div><dt>Safeguard review</dt><dd>Permitted at draft creation; checked again before submission.</dd></div>
              <div><dt>Expires</dt><dd>{new Date(draft.expires_at).toLocaleString()}</dd></div>
              <div><dt>Fingerprint</dt><dd>{draft.fingerprint}</dd></div>
            </dl>
            {draft.warnings.length > 0 && <p>Warnings: {draft.warnings.join(", ")}</p>}
          </div>
          <button type="button" disabled={confirm.isPending || order !== null} onClick={() => confirm.mutate()}>
            {confirm.isPending ? "Confirming…" : "Confirm fake order"}
          </button>
        </article>
      )}
      {confirm.isError && <p className="inline-alert" role="alert">Confirmation was not accepted. Create a new draft if it expired or changed.</p>}
      {outcome !== null && <p className={order?.state === "UNKNOWN" ? "inline-alert" : "state"} role="status">{outcome}</p>}
      {order?.state === "REJECTED" && order.result.message !== null && (
        <p className="inline-alert" role="alert">{order.result.message}</p>
      )}
    </section>
  );
}
