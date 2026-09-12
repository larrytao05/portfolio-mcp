import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const api = vi.hoisted(() => ({
  getAccountPositions: vi.fn(),
  getAccounts: vi.fn(),
  getHealth: vi.fn(),
  getLatestRefresh: vi.fn(),
  getQuote: vi.fn(),
  refreshPortfolio: vi.fn(),
  searchInstruments: vi.fn(),
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
    api.getLatestRefresh.mockResolvedValue({ refresh: null });
    api.searchInstruments.mockResolvedValue({ instruments: [] });
    api.getQuote.mockResolvedValue({ quote: null });
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

  it("shows no-match and unavailable quote states", async () => {
    api.searchInstruments
      .mockResolvedValueOnce({ instruments: [] })
      .mockResolvedValueOnce({
        instruments: [
          {
            id: "us-fund:FIXTURE_UNAVAILABLE",
            symbol: "FIXTURE_UNAVAILABLE",
            name: "Fixture Unavailable Price Fund",
            asset_class: "mutual_fund",
            exchange: null,
            currency: "USD",
          },
        ],
      });
    api.getQuote.mockResolvedValue({
      quote: {
        instrument: {
          id: "us-fund:FIXTURE_UNAVAILABLE",
          symbol: "FIXTURE_UNAVAILABLE",
          name: "Fixture Unavailable Price Fund",
          asset_class: "mutual_fund",
          exchange: null,
          currency: "USD",
        },
        source: "fixture_market_data",
        observed_at: "2026-09-12T20:00:00+00:00",
        last_price: null,
        bid_price: null,
        ask_price: null,
        currency: "USD",
      },
    });
    renderApp();

    const input = screen.getByLabelText("Search instruments");
    fireEvent.change(input, { target: { value: "not-a-symbol" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("No instruments found.")).toBeTruthy();

    fireEvent.change(input, { target: { value: "unavailable" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /FIXTURE_UNAVAILABLE/ }),
    );

    expect(await screen.findByText("Canonical identity: us-fund:FIXTURE_UNAVAILABLE")).toBeTruthy();
    expect(await screen.findByText("Source: fixture_market_data")).toBeTruthy();
    expect(await screen.findByText(/Last price: unavailable USD/)).toBeTruthy();
    expect(await screen.findByText(/Observed:/)).toBeTruthy();
  });

  it("shows a safe message when a provider-backed search fails", async () => {
    api.searchInstruments.mockRejectedValue(new Error("provider failure"));
    renderApp();

    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("Instrument search is unavailable.")).toBeTruthy();
  });
});
