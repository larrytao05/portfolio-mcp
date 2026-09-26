export type Account = {
  id: string;
  provider: string;
  label: string;
  account_type: string;
  currency: string;
  is_stale: boolean;
  source_refreshed_at: string;
};

type AccountsResponse = {
  accounts: Account[];
};

export type RefreshResult = {
  id: number;
  status: string;
  started_at: string;
  completed_at: string;
  accounts_refreshed: number;
  positions_refreshed: number;
  daily_snapshots_recorded: number;
  error_code: string | null;
  error_message: string | null;
  provider_outcomes: ProviderRefreshOutcome[];
  warnings: string[];
};

export type ProviderRefreshOutcome = {
  provider: string;
  status: string;
  accounts_refreshed: number;
  stale_accounts: number;
  excluded_accounts: number;
  warning: string | null;
};

export type Position = {
  account_id: string;
  as_of: string;
  symbol: string;
  name: string;
  asset_class: string;
  quantity: string;
  current_price: string | null;
  market_value: string | null;
  cost_basis: string | null;
  gain_loss: string | null;
  currency: string;
  is_stale: boolean;
  source_refreshed_at: string;
};

export type AccountDetail = Account & {
  refreshed_at: string;
  as_of: string | null;
  balances: {
    market_value: string | null;
    cost_basis: string | null;
    currency: string;
  };
  positions: Position[];
};

export type Activity = {
  id: number;
  account: { id: string; label: string };
  provider: string;
  occurred_on: string;
  occurred_at: string | null;
  type: string;
  symbol: string | null;
  description: string;
  quantity: string | null;
  amount: string;
  fees: string;
  currency: string;
  imported_at: string;
};

export type ActivityFilters = {
  account_id?: string;
  provider?: string;
  type?: string;
  symbol?: string;
  start_date?: string;
  end_date?: string;
  limit?: number;
  offset?: number;
};

export type Instrument = {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  exchange: string | null;
  currency: string | null;
};

export type Quote = {
  instrument: Instrument;
  source: string;
  observed_at: string;
  last_price: string | null;
  bid_price: string | null;
  ask_price: string | null;
  currency: string | null;
};

export type CapabilityBlock = {
  code: string;
  message: string;
  recovery_action: string | null;
};

export type ProviderHealth = {
  provider: string;
  state: string;
  observed_at: string | null;
  last_success_at: string | null;
  blocks: CapabilityBlock[];
};

export type AccountCapability = {
  account_id: string;
  provider: string;
  asset_classes: string[];
  supported_sides: string[];
  order_types: string[];
  time_in_force: string[];
  sizing_modes: string[];
  preview_supported: boolean;
  cancellation_supported: boolean;
  observed_at: string | null;
  last_success_at: string | null;
  source: string;
  blocks: CapabilityBlock[];
  is_stale: boolean;
  is_trade_capable: boolean;
};

export type TradingSettings = {
  live_trading_enabled: boolean;
  kill_switch_active: boolean;
  max_order_shares: string | null;
  max_order_notional_usd: string | null;
  updated_at: string | null;
  version: number;
  effective_state: string;
};

export type OrderDraft = {
  id: string;
  account: { id: string; label: string; provider: string };
  instrument: { id: string; symbol: string; name: string; asset_class: string };
  instruction: {
    side: string;
    type: string;
    quantity: string;
    limit_price: string | null;
    time_in_force: string;
  };
  quote: {
    observed_at: string | null;
    last_price: string | null;
    bid_price: string | null;
    ask_price: string | null;
    source: string | null;
  };
  safety: {
    estimated_notional: string | null;
    account_refreshed_at: string | null;
    capability_observed_at: string | null;
    capability_last_success_at: string | null;
  };
  warnings: string[];
  fingerprint: string;
  created_at: string;
  expires_at: string;
};

export type Order = {
  id: string;
  draft_id: string;
  fingerprint: string;
  state: "ACCEPTED" | "REJECTED" | "UNKNOWN" | string;
  result: { code: string | null; message: string | null };
};

export type OverviewAccountContribution = {
  account_id: string;
  label: string;
  provider: string;
  account_type: string;
  currency: string;
  market_value: string | null;
  is_stale: boolean;
  percentage_of_total: string | null;
  percentage_of_total_display?: string | null;
};

export type AllocationSlice = {
  key: string;
  label: string;
  amount: string;
  percentage: string;
  percentage_display?: string;
  position_count: number;
};

export type AllocationGroup = {
  group_by: string;
  denominator: string;
  slices: AllocationSlice[];
  included_count: number;
  excluded_count: number;
};

export type GainLossCoverage = {
  unrealized_gain_loss: string | null;
  cost_basis: string | null;
  market_value: string | null;
  included_count: number;
  excluded_count: number;
};

export type OverviewExclusion = {
  reason: string;
  symbol: string | null;
  account_id: string | null;
  details: string;
};

export type DailyRecordedPoint = {
  date: string;
  snapshot_date?: string;
  value: string;
  currency: string;
  accounts_count: number;
  accounts_total: number;
  is_complete: boolean;
};

export type PortfolioOverview = {
  total_known_usd_value: string | null;
  cash_usd?: string | null;
  buying_power_usd?: string | null;
  as_of: string | null;
  refreshed_at: string | null;
  status: "fresh" | "stale" | "partial" | "empty" | string;
  accounts: OverviewAccountContribution[];
  allocations: {
    account?: AllocationGroup;
    asset_class?: AllocationGroup;
    security_type?: AllocationGroup;
    [key: string]: AllocationGroup | undefined;
  };
  gain_loss: GainLossCoverage;
  exclusions: OverviewExclusion[];
  warnings: string[];
  history: DailyRecordedPoint[];
};

export type OverviewResponse = {
  overview: PortfolioOverview;
};

export type HistoryResponse = {
  history: DailyRecordedPoint[];
};
export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = body?.error?.message;
    const code = body?.error?.code;
    throw new ApiError(
      response.status,
      typeof message === "string"
        ? message
        : `Request failed with status ${response.status}`,
      typeof code === "string" ? code : undefined,
    );
  }

  return response.json() as Promise<T>;
}

export function getHealth(): Promise<{ status: string }> {
  return getJson("/api/health");
}

export function getAccounts(): Promise<AccountsResponse> {
  return getJson("/api/accounts");
}

export function getAccount(accountId: string): Promise<{ account: AccountDetail }> {
  return getJson(`/api/accounts/${encodeURIComponent(accountId)}`);
}

export function getLatestRefresh(): Promise<{ refresh: RefreshResult | null }> {
  return getJson("/api/refreshes/latest");
}

export function getTradingStatus(): Promise<{
  providers: ProviderHealth[];
  accounts: AccountCapability[];
}> {
  return getJson("/api/trading/status");
}

export function getTradingSettings(): Promise<{ settings: TradingSettings }> {
  return getJson("/api/trading/settings");
}

export function updateTradingSettings(input: Omit<TradingSettings, "updated_at" | "effective_state">): Promise<{ settings: TradingSettings }> {
  return getJson("/api/trading/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function refreshPortfolio(): Promise<{ refresh: RefreshResult }> {
  return getJson("/api/refresh", { method: "POST" });
}

export function getActivity(filters: ActivityFilters = {}): Promise<{
  activities: Activity[];
  pagination: { limit: number; offset: number; total: number };
}> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return getJson(`/api/activity${query ? `?${query}` : ""}`);
}

export function searchInstruments(query: string): Promise<{ instruments: Instrument[] }> {
  return getJson(`/api/instruments/search?query=${encodeURIComponent(query)}`);
}

export function getQuote(instrumentId: string): Promise<{ quote: Quote }> {
  return getJson(`/api/instruments/${encodeURIComponent(instrumentId)}/quote`);
}

export function createOrderDraft(input: {
  account_id: string;
  instrument_id: string;
  side: string;
  order_type: string;
  quantity: string;
  limit_price?: string | null;
}): Promise<{ draft: OrderDraft }> {
  return getJson("/api/order-drafts", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function confirmOrderDraft(
  draftId: string,
  expectedFingerprint: string,
): Promise<{ order: Order }> {
  return getJson(`/api/order-drafts/${encodeURIComponent(draftId)}/confirm`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ expected_fingerprint: expectedFingerprint, confirmed: true }),
  });
}

export function getOverview(): Promise<OverviewResponse> {
  return getJson("/api/overview");
}

export function getOverviewHistory(): Promise<HistoryResponse> {
  return getJson("/api/overview/history");
}

export type StoredOrder = {
  id: string;
  draft_id: string;
  fingerprint: string;
  account: {
    id: string;
    label: string;
  };
  provider: string;
  instrument: {
    id: string;
    symbol: string;
  };
  instruction: {
    side: "buy" | "sell" | string;
    type: "market" | "limit" | string;
    quantity: string;
    limit_price: string | null;
    time_in_force?: string;
  };
  state: string;
  broker_order_id: string | null;
  result: {
    code: string | null;
    message: string | null;
    source: string | null;
  };
  fill: {
    quantity: string;
    average_price: string | null;
  } | null;
  remaining_quantity?: string | null;
  provider_submission_started_at?: string | null;
  provider_updated_at: string | null;
  provider_status_label: string | null;
  created_at: string;
  updated_at: string;
  version: number;
  can_cancel?: boolean;
  blocking_reason?: string | null;
  reconciliation?: {
    status: string;
    source: string | null;
    provider_updated_at: string | null;
    next_refresh_at: string | null;
    target_order_id: string | null;
  };
};

export type OrderRefreshGroup = {
  provider: string;
  account_id: string;
  target_order_id: string | null;
  next_refresh_at: string;
};

export type OrderListPage = {
  orders: StoredOrder[];
  next_cursor: string | null;
  refresh_groups: OrderRefreshGroup[];
  server_time: string;
};

export type OrderAuditEvent = {
  event_id: string;
  draft_id: string | null;
  order_id: string | null;
  account_id: string | null;
  event_type: string;
  actor: string;
  previous_state: string | null;
  next_state: string | null;
  code: string | null;
  details: Record<string, unknown>;
  occurred_at: string;
};

export type OrderAuditPage = {
  events: OrderAuditEvent[];
  next_cursor: string | null;
};

export type RefreshOrderResult = {
  order: StoredOrder;
  refresh: {
    status: "attempted" | "throttled" | "target_changed" | "not_refreshable" | string;
    provider_read_started: boolean;
    next_refresh_at: string | null;
    target_order_id: string | null;
    server_time: string;
  };
};

export type OrderListFilters = {
  account_id?: string;
  provider?: string;
  symbol?: string;
  state?: string[];
  start_date?: string;
  end_date?: string;
  limit?: number;
  cursor?: string;
};

export type OrderAuditFilters = {
  order_id?: string;
  draft_id?: string;
  account_id?: string;
  provider?: string;
  symbol?: string;
  state?: string[];
  start_date?: string;
  end_date?: string;
  limit?: number;
  cursor?: string;
};

export function getOrders(filters: OrderListFilters = {}): Promise<OrderListPage> {
  const params = new URLSearchParams();
  if (filters.account_id) params.set("account_id", filters.account_id);
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.symbol) params.set("symbol", filters.symbol);
  if (filters.state) {
    for (const s of filters.state) params.append("state", s);
  }
  if (filters.start_date) params.set("start_date", filters.start_date);
  if (filters.end_date) params.set("end_date", filters.end_date);
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.cursor) params.set("cursor", filters.cursor);
  const qs = params.toString();
  return getJson(`/api/orders${qs ? `?${qs}` : ""}`);
}

export function getOrderAudit(filters: OrderAuditFilters = {}): Promise<OrderAuditPage> {
  const params = new URLSearchParams();
  if (filters.order_id) params.set("order_id", filters.order_id);
  if (filters.draft_id) params.set("draft_id", filters.draft_id);
  if (filters.account_id) params.set("account_id", filters.account_id);
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.symbol) params.set("symbol", filters.symbol);
  if (filters.state) {
    for (const s of filters.state) params.append("state", s);
  }
  if (filters.start_date) params.set("start_date", filters.start_date);
  if (filters.end_date) params.set("end_date", filters.end_date);
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.cursor) params.set("cursor", filters.cursor);
  const qs = params.toString();
  return getJson(`/api/order-audit${qs ? `?${qs}` : ""}`);
}

export function refreshOrder(
  orderId: string,
  mode: "scheduled" | "manual" = "manual",
): Promise<RefreshOrderResult> {
  return getJson(`/api/orders/${encodeURIComponent(orderId)}/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ mode }),
  });
}

export type ConfirmOrderCancellationInput = {
  expected_version: number;
  expected_state: string;
  confirmed: boolean;
};

export type ConfirmOrderCancellationResponse = {
  order: StoredOrder;
};

export function confirmOrderCancellation(
  orderId: string,
  input: ConfirmOrderCancellationInput,
): Promise<ConfirmOrderCancellationResponse> {
  return getJson(`/api/orders/${encodeURIComponent(orderId)}/cancel/confirm`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function getOrder(orderId: string): Promise<{ order: StoredOrder }> {
  return getJson(`/api/orders/${encodeURIComponent(orderId)}`);
}

