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
};

export type AllocationSlice = {
  key: string;
  label: string;
  amount: string;
  percentage: string;
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
  date?: string;
  snapshot_date?: string;
  value: string;
  currency: string;
  accounts_count: number;
  accounts_total?: number;
  is_complete?: boolean;
};

export type RecordedHistory = {
  points: DailyRecordedPoint[];
  currencies: string[];
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
  history: RecordedHistory;
};

export type OverviewResponse = {
  overview: PortfolioOverview;
};

export type HistoryResponse =
  | { history: RecordedHistory | DailyRecordedPoint[] }
  | DailyRecordedPoint[]
  | RecordedHistory;


async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getHealth(): Promise<{ status: string }> {
  return getJson("/api/health");
}

export function getAccounts(): Promise<AccountsResponse> {
  return getJson("/api/accounts");
}

export function getAccountPositions(accountId: string): Promise<{ positions: Position[] }> {
  return getJson(`/api/accounts/${encodeURIComponent(accountId)}/positions`);
}

export function getAccount(accountId: string): Promise<{ account: AccountDetail }> {
  return getJson(`/api/accounts/${encodeURIComponent(accountId)}`);
}

export function getLatestRefresh(): Promise<{ refresh: RefreshResult | null }> {
  return getJson("/api/refreshes/latest");
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
