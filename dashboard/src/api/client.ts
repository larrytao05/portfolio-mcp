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

export function refreshPortfolio(): Promise<{ refresh: RefreshResult }> {
  return getJson("/api/refresh", { method: "POST" });
}
