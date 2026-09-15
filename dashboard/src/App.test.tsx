import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { Position } from "./api/client";

const api = vi.hoisted(() => ({
  confirmOrderDraft: vi.fn(),
  createOrderDraft: vi.fn(),
  getAccount: vi.fn(),
  getActivity: vi.fn(),
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
      balances: {
        market_value: "3041.12",
        cost_basis: "2781.00",
        currency: "USD",
      },
      positions,
    },
  };
}

describe("App", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    api.getHealth.mockResolvedValue({ status: "ok" });
    api.getAccounts.mockResolvedValue({ accounts: [] });
    api.getAccount.mockResolvedValue(savedAccount());
    api.getActivity.mockResolvedValue({
      activities: [],
      pagination: { limit: 50, offset: 0, total: 0 },
    });
    api.getLatestRefresh.mockResolvedValue({ refresh: null });
    api.searchInstruments.mockResolvedValue({ instruments: [] });
    api.getQuote.mockResolvedValue({ quote: null });
    api.createOrderDraft.mockResolvedValue({ draft: null });
    api.confirmOrderDraft.mockResolvedValue({ order: null });
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
        provider_outcomes: [],
        warnings: [],
      },
    });
  });

  it("refreshes persisted portfolio data from the dashboard", async () => {
    renderApp();

    fireEvent.click(
      await screen.findByRole("button", { name: "Refresh portfolio" }),
    );

    await waitFor(() => expect(api.refreshPortfolio).toHaveBeenCalledOnce());
    expect(
      await screen.findByText(/Saved 2 accounts and 9 positions/),
    ).toBeTruthy();
  });

  it.each([
    ["UNKNOWN", /Reconciliation is required/],
    ["PARTIALLY_FILLED", /partially filled the order/],
    ["FILLED", /filled the order/],
  ])("reviews and confirms a fake order with %s outcome", async (state, message) => {
    api.getAccounts.mockResolvedValue({
      accounts: [
        {
          ...savedAccount().account,
          is_stale: false,
          source_refreshed_at: new Date().toISOString(),
        },
      ],
    });
    api.createOrderDraft.mockResolvedValue({
      draft: {
        id: "draft-1",
        account: {
          id: "schwab-taxable-demo",
          label: "Schwab Taxable ••••4821",
          provider: "Schwab",
        },
        instrument: {
          id: "us-etf:VTI",
          symbol: "VTI",
          name: "Vanguard Total Stock Market ETF",
          asset_class: "equity_etf",
        },
        instruction: {
          side: "buy",
          type: "limit",
          quantity: "1",
          limit_price: "333.33",
          time_in_force: "day",
        },
        quote: { observed_at: null, last_price: null, bid_price: null, ask_price: null, source: null },
        warnings: ["preview_unavailable", "impact_unavailable"],
        fingerprint: "safe-fingerprint",
        created_at: "2026-09-12T20:00:00Z",
        expires_at: "2026-09-12T20:05:00Z",
      },
    });
    api.confirmOrderDraft.mockResolvedValue({
      order: {
        id: "order-1",
        draft_id: "draft-1",
        fingerprint: "safe-fingerprint",
        state,
        result: { code: state.toLowerCase(), message: state },
      },
    });
    renderApp();

    await waitFor(() =>
      expect(
        screen.getAllByRole("option", { name: "Schwab Taxable ••••4821" }),
      ).toHaveLength(2),
    );
    fireEvent.change(await screen.findByLabelText("Trade account"), {
      target: { value: "schwab-taxable-demo" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review fake order" }));
    await waitFor(() => expect(api.createOrderDraft).toHaveBeenCalledOnce());
    expect(await screen.findByRole("button", { name: "Confirm fake order" })).toBeTruthy();
    expect(screen.getAllByText("Canonical instrument ID")).toHaveLength(2);
    expect(screen.getByText("us-etf:VTI")).toBeTruthy();
    expect(screen.getByText("Time in force")).toBeTruthy();
    expect(screen.getByText(/Fake execution provider/)).toBeTruthy();
    expect(screen.getByText(/preview_unavailable/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm fake order" }));
    await waitFor(() => expect(api.confirmOrderDraft).toHaveBeenCalledWith("draft-1", "safe-fingerprint"));
    expect(await screen.findByText(message)).toBeTruthy();
    if (state === "UNKNOWN") expect(screen.queryByText(/retry/i)).toBeNull();
  });

  it("does not show a missing record or search loading while queries are inactive", async () => {
    api.getLatestRefresh.mockReturnValue(new Promise(() => {}));
    renderApp();
    expect(await screen.findByText("Loading saved record…")).toBeTruthy();
    expect(screen.queryByText("No saved record yet")).toBeNull();
    expect(screen.queryByText("Searching instruments…")).toBeNull();
    expect(screen.queryByText("Loading quote…")).toBeNull();
  });

  it("keeps saved account identity visible when detail loading fails", async () => {
    api.getAccounts.mockResolvedValue({
      accounts: [
        {
          ...savedAccount().account,
          is_stale: false,
          source_refreshed_at: new Date().toISOString(),
        },
      ],
    });
    api.getAccount.mockRejectedValue(new Error("unavailable"));
    renderApp();
    expect(
      await screen.findByText(/Saved account details are unavailable/),
    ).toBeTruthy();
    expect(
      screen.getByText("Schwab Taxable ••••4821", { selector: "strong" }),
    ).toBeTruthy();
  });

  it("shows persisted coverage and warnings before any refresh action", async () => {
    api.getLatestRefresh.mockResolvedValue({
      refresh: {
        id: 8,
        status: "partial",
        completed_at: new Date().toISOString(),
        accounts_refreshed: 1,
        positions_refreshed: 2,
        provider_outcomes: [
          {
            provider: "Schwab",
            accounts_refreshed: 1,
            stale_accounts: 1,
            excluded_accounts: 2,
          },
        ],
        warnings: ["Two accounts could not be imported."],
      },
    });
    renderApp();
    expect(
      await screen.findByText("Two accounts could not be imported."),
    ).toBeTruthy();
    expect(screen.getByText(/1 refreshed, 1 stale, 2 excluded/)).toBeTruthy();
    expect(api.refreshPortfolio).not.toHaveBeenCalled();
  });

  it("marks an old successful refresh as stale", async () => {
    api.getLatestRefresh.mockResolvedValue({
      refresh: {
        id: 8,
        status: "success",
        completed_at: "2020-01-01T00:00:00Z",
        accounts_refreshed: 0,
        positions_refreshed: 0,
        provider_outcomes: [],
        warnings: [],
      },
    });
    renderApp();
    expect(await screen.findByText("Saved record is stale")).toBeTruthy();
    expect(screen.queryByText("Saved record is current")).toBeNull();
  });

  it("reports unavailable refresh status instead of claiming an empty record", async () => {
    api.getLatestRefresh.mockRejectedValue(new Error("unavailable"));
    renderApp();
    expect(
      await screen.findByText(/Saved record status is unavailable/),
    ).toBeTruthy();
    expect(screen.queryByText("No saved record yet")).toBeNull();
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
          is_stale: false,
          source_refreshed_at: "2026-09-12T14:00:01+00:00",
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
          is_stale: false,
          source_refreshed_at: "2026-09-12T14:00:01+00:00",
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
        provider_outcomes: [],
        warnings: [],
      },
    });

    renderApp();

    expect(
      await screen.findByText("Schwab Taxable ••••4821", {
        selector: "strong",
      }),
    ).toBeTruthy();
    expect(await screen.findByText(/9.123456789123456789/)).toBeTruthy();
    expect(screen.getAllByText("Unavailable")).toHaveLength(4);
    fireEvent.change(screen.getByLabelText("Filter holdings"), {
      target: { value: "VTI" },
    });
    expect(screen.queryByText("MISSING", { selector: "strong" })).toBeNull();
    expect(screen.getByText("VTI", { selector: "strong" })).toBeTruthy();
    expect(screen.getByText(/Vanguard Total Stock Market ETF/)).toBeTruthy();
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

    expect(
      await screen.findByText("This account has no saved holdings."),
    ).toBeTruthy();
    expect(screen.getAllByText(/Stale saved data/).length).toBeGreaterThan(0);
  });

  it("sorts fractional and negative holding values numerically", async () => {
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
          symbol: "HIGH",
          name: "High fractional holding",
          asset_class: "equity",
          quantity: "2.9",
          current_price: "1",
          market_value: "1",
          cost_basis: "1",
          gain_loss: "1",
          currency: "USD",
          is_stale: false,
          source_refreshed_at: "2026-09-12T14:00:01+00:00",
        },
        {
          account_id: "schwab-taxable-demo",
          as_of: "2026-09-12",
          symbol: "LOW",
          name: "Low fractional holding",
          asset_class: "equity",
          quantity: "2.10",
          current_price: "1",
          market_value: "1",
          cost_basis: "1",
          gain_loss: "-1.5",
          currency: "USD",
          is_stale: false,
          source_refreshed_at: "2026-09-12T14:00:01+00:00",
        },
      ]),
    );

    renderApp();

    fireEvent.click(
      await screen.findByRole("button", { name: "Sort by Quantity" }),
    );
    expect(
      screen
        .getAllByRole("row")
        .slice(1)
        .map((row) => row.textContent),
    ).toEqual([
      expect.stringContaining("LOW"),
      expect.stringContaining("HIGH"),
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Sort by Gain/loss" }));
    expect(
      screen
        .getAllByRole("row")
        .slice(1)
        .map((row) => row.textContent),
    ).toEqual([
      expect.stringContaining("LOW"),
      expect.stringContaining("HIGH"),
    ]);
  });

  it("discloses stale provider data after a partial refresh", async () => {
    api.refreshPortfolio.mockResolvedValue({
      refresh: {
        id: 2,
        status: "partial",
        started_at: "2026-09-12T15:00:00+00:00",
        completed_at: "2026-09-12T15:00:01+00:00",
        accounts_refreshed: 1,
        positions_refreshed: 5,
        daily_snapshots_recorded: 1,
        error_code: null,
        error_message: null,
        provider_outcomes: [],
        warnings: ["Schwab data is stale; last successful data is shown."],
      },
    });

    renderApp();
    fireEvent.click(
      await screen.findByRole("button", { name: "Refresh portfolio" }),
    );

    expect(await screen.findByText(/Refresh partial/)).toBeTruthy();
    expect(
      await screen.findByText(
        "Schwab data is stale; last successful data is shown.",
      ),
    ).toBeTruthy();
  });

  it("keeps saved data visible after a failed refresh", async () => {
    api.getAccounts.mockResolvedValue({
      accounts: [
        {
          id: "schwab-taxable-demo",
          provider: "Schwab",
          label: "Schwab Taxable ••••4821",
          account_type: "taxable_brokerage",
          currency: "USD",
          is_stale: true,
          source_refreshed_at: "2026-09-12T14:00:01+00:00",
        },
      ],
    });
    api.getLatestRefresh.mockResolvedValue({
      refresh: {
        id: 2,
        status: "failed",
        started_at: "2026-09-12T15:00:00+00:00",
        completed_at: "2026-09-12T15:00:01+00:00",
        accounts_refreshed: 0,
        positions_refreshed: 0,
        daily_snapshots_recorded: 0,
        error_code: "provider_error",
        error_message: "Fixture provider is unavailable",
        provider_outcomes: [],
        warnings: ["Schwab data is stale; last successful data is shown."],
      },
    });
    api.refreshPortfolio.mockResolvedValue({
      refresh: {
        id: 2,
        status: "failed",
        started_at: "2026-09-12T15:00:00+00:00",
        completed_at: "2026-09-12T15:00:01+00:00",
        accounts_refreshed: 0,
        positions_refreshed: 0,
        daily_snapshots_recorded: 0,
        error_code: "provider_error",
        error_message: "Fixture provider is unavailable",
        provider_outcomes: [],
        warnings: ["Schwab data is stale; last successful data is shown."],
      },
    });

    renderApp();
    fireEvent.click(
      await screen.findByRole("button", { name: "Refresh portfolio" }),
    );

    expect(
      (await screen.findAllByText(/Refresh failed/)).length,
    ).toBeGreaterThan(0);
    expect(
      (await screen.findAllByText(/Stale saved data/)).length,
    ).toBeGreaterThan(0);
    expect(
      await screen.findByText(
        "Schwab data is stale; last successful data is shown.",
      ),
    ).toBeTruthy();
  });

  it("shows an empty filtered activity feed", async () => {
    renderApp();

    expect(
      await screen.findByText("No activity matches these filters."),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Activity symbol"), {
      target: { value: "NOT-A-SYMBOL" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Apply activity filters" }),
    );

    await waitFor(() =>
      expect(api.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ symbol: "NOT-A-SYMBOL" }),
      ),
    );
  });

  it("shows populated activity and navigates between pages", async () => {
    api.getActivity.mockImplementation(({ offset = 0 }) =>
      Promise.resolve({
        activities: [
          {
            id: offset + 1,
            account: {
              id: "schwab-taxable-demo",
              label: "Schwab Taxable ••••4821",
            },
            provider: "Schwab",
            occurred_on: "2026-09-12",
            occurred_at: "2026-09-12T14:00:00+00:00",
            type: "TRADE",
            symbol: offset === 0 ? "VTI" : "VXUS",
            description: "Fixture trade",
            quantity: "1",
            amount: "100",
            fees: "0",
            currency: "USD",
            imported_at: "2026-09-12T14:01:00+00:00",
          },
        ],
        pagination: { limit: 50, offset, total: 51 },
      }),
    );

    renderApp();

    expect(await screen.findByText(/VTI/)).toBeTruthy();
    expect(screen.getByText("Showing 1-50 of 51")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next activity page" }));
    expect(await screen.findByText(/VXUS/)).toBeTruthy();
    expect(screen.getByText("Showing 51-51 of 51")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Previous activity page" }),
    );
    expect(await screen.findByText(/VTI/)).toBeTruthy();
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

    expect(await screen.findByText(/No instruments found\./)).toBeTruthy();

    fireEvent.change(input, { target: { value: "unavailable" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /FIXTURE_UNAVAILABLE/ }),
    );

    const quote = await screen.findByRole("article", {
      name: "Instrument quote",
    });
    expect(quote.textContent).toContain(
      "Canonical identity: us-fund:FIXTURE_UNAVAILABLE",
    );
    expect(quote.textContent).toContain("Source: fixture_market_data");
    expect(quote.textContent).toContain("unavailable USD");
    expect(await screen.findByText(/Observed:/)).toBeTruthy();
  });

  it("shows a safe message when a provider-backed search fails", async () => {
    api.searchInstruments.mockRejectedValue(new Error("provider failure"));
    renderApp();

    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(
      await screen.findByText(/Instrument search is unavailable\./),
    ).toBeTruthy();
    api.searchInstruments.mockResolvedValue({ instruments: [] });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText(/No instruments found\./)).toBeTruthy();
    expect(api.searchInstruments).toHaveBeenCalledTimes(2);
  });
});
