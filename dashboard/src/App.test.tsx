import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { Position } from "./api/client";

const api = vi.hoisted(() => ({
  getAccount: vi.fn(),
  getAccounts: vi.fn(),
  getHealth: vi.fn(),
  getLatestRefresh: vi.fn(),
  refreshPortfolio: vi.fn(),
}));

vi.mock("./api/client", () => api);

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

function savedAccount(positions: Position[] = []) {
  return {
    account: {
      id: "schwab-taxable-demo",
      provider: "Schwab",
      label: "Schwab Taxable ••••4821",
      account_type: "taxable_brokerage",
      currency: "USD",
      refreshed_at: "2026-09-12T14:00:01+00:00",
      as_of: "2026-09-12",
      balances: { market_value: "3041.12", cost_basis: "2781.00", currency: "USD" },
      positions,
    },
  };
}

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getHealth.mockResolvedValue({ status: "ok" });
    api.getAccounts.mockResolvedValue({ accounts: [] });
    api.getAccount.mockResolvedValue(savedAccount());
    api.getLatestRefresh.mockResolvedValue({ refresh: null });
    api.refreshPortfolio.mockResolvedValue({
      refresh: {
        id: 1,
        status: "success",
        started_at: "2026-09-12T14:00:00+00:00",
        completed_at: "2026-09-12T14:00:01+00:00",
        accounts_refreshed: 2,
        positions_refreshed: 9,
        daily_snapshots_recorded: 2,
        error_code: null,
        error_message: null,
      },
    });
  });

  it("refreshes persisted portfolio data from the dashboard", async () => {
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "Refresh portfolio" }));

    await waitFor(() => expect(api.refreshPortfolio).toHaveBeenCalledOnce());
    expect(await screen.findByText(/Saved 2 accounts and 9 positions/)).toBeTruthy();
  });

  it("shows saved account details and lets holdings be filtered", async () => {
    api.getAccounts.mockResolvedValue({
      accounts: [
        {
          id: "schwab-taxable-demo",
          provider: "Schwab",
          label: "Schwab Taxable ••••4821",
          account_type: "taxable_brokerage",
          currency: "USD",
        },
      ],
    });
    api.getAccount.mockResolvedValue(
      savedAccount([
        {
          account_id: "schwab-taxable-demo",
          as_of: "2026-09-12",
          symbol: "VTI",
          name: "Vanguard Total Stock Market ETF",
          asset_class: "equity_etf",
          quantity: "9.123456789123456789",
          current_price: "333.33",
          market_value: "3041.12",
          cost_basis: "2781.00",
          gain_loss: "260.12",
          currency: "USD",
        },
        {
          account_id: "schwab-taxable-demo",
          as_of: "2026-09-12",
          symbol: "MISSING",
          name: "Missing price fund",
          asset_class: "fund",
          quantity: "1",
          current_price: null,
          market_value: null,
          cost_basis: null,
          gain_loss: null,
          currency: "USD",
        },
      ]),
    );
    api.getLatestRefresh.mockResolvedValue({
      refresh: {
        id: 1,
        status: "success",
        started_at: "2026-09-12T14:00:00+00:00",
        completed_at: "2026-09-12T14:00:01+00:00",
        accounts_refreshed: 1,
        positions_refreshed: 2,
        daily_snapshots_recorded: 1,
        error_code: null,
        error_message: null,
      },
    });

    renderApp();

    expect(await screen.findByText("Schwab Taxable ••••4821")).toBeTruthy();
    expect(await screen.findByText(/9.123456789123456789/)).toBeTruthy();
    expect(screen.getAllByText("Unavailable")).toHaveLength(4);
    fireEvent.change(screen.getByLabelText("Filter holdings"), {
      target: { value: "VTI" },
    });
    expect(screen.queryByText(/MISSING — Missing price fund/)).toBeNull();
    expect(screen.getByText(/VTI — Vanguard Total Stock Market ETF/)).toBeTruthy();
  });

  it("shows an empty account and marks stale saved data", async () => {
    api.getAccounts.mockResolvedValue({
      accounts: [
        {
          id: "schwab-taxable-demo",
          provider: "Schwab",
          label: "Schwab Taxable ••••4821",
          account_type: "taxable_brokerage",
          currency: "USD",
        },
      ],
    });
    api.getAccount.mockResolvedValue({
      account: {
        ...savedAccount().account,
        refreshed_at: "2020-01-02T00:00:00+00:00",
        as_of: null,
        balances: { market_value: "0", cost_basis: "0", currency: "USD" },
        positions: [],
      },
    });

    renderApp();

    expect(await screen.findByText("This account has no saved holdings.")).toBeTruthy();
    expect(screen.getByText(/Stale saved data/)).toBeTruthy();
  });
});
