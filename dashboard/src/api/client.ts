export type Account = {
  id: string;
  provider: string;
  label: string;
  account_type: string;
  currency: string;
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
  currency: string;
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
