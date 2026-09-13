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
  currency: string;
  is_stale: boolean;
  source_refreshed_at: string;
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
