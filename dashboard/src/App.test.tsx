import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const api = vi.hoisted(() => ({
  getAccountPositions: vi.fn(),
  getActivity: vi.fn(),
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

describe("App", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    api.getHealth.mockResolvedValue({ status: "ok" });
    api.getAccounts.mockResolvedValue({ accounts: [] });
    api.getAccountPositions.mockResolvedValue({ positions: [] });
    api.getActivity.mockResolvedValue({
      activities: [],
      pagination: { limit: 50, offset: 0, total: 0 },
    });
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

  it("shows portfolio data saved by an earlier app session", async () => {
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
    api.getAccountPositions.mockResolvedValue({
      positions: [
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
          currency: "USD",
        },
      ],
    });
    api.getLatestRefresh.mockResolvedValue({
      refresh: {
        id: 1,
        status: "success",
        started_at: "2026-09-12T14:00:00+00:00",
        completed_at: "2026-09-12T14:00:01+00:00",
        accounts_refreshed: 1,
        positions_refreshed: 1,
        daily_snapshots_recorded: 1,
        error_code: null,
        error_message: null,
      },
    });

    renderApp();

    expect(await screen.findByText(/Last saved refresh: success/)).toBeTruthy();
    expect(await screen.findByText(/9.123456789123456789 shares/)).toBeTruthy();
  });

  it("shows an empty filtered activity feed", async () => {
    renderApp();

    expect(await screen.findByText("No activity matches these filters.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Activity symbol"), {
      target: { value: "NOT-A-SYMBOL" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply activity filters" }));

    await waitFor(() =>
      expect(api.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ symbol: "NOT-A-SYMBOL" }),
      ),
    );
  });
});
