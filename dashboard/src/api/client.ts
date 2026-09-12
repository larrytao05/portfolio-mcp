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

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
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
