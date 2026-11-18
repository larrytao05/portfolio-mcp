import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAccount, type Account, type AccountDetail, type Position } from "../../api/client";
import { PositionsSection } from "./PositionsSection";

vi.mock("../../api/client", () => ({ getAccount: vi.fn() }));
const accounts: Account[] = ["Schwab", "Fidelity"].map((provider) => ({
  id: provider, provider, label: `${provider} Brokerage`, account_type: "taxable_brokerage", currency: "USD", is_stale: false, source_refreshed_at: new Date().toISOString(),
}));
function detail(account: Account, quantity: string, overrides: Partial<Position> = {}): AccountDetail {
  return { ...account, refreshed_at: account.source_refreshed_at, as_of: "2026-11-10", balances: { currency: "USD", market_value: "100", cost_basis: null }, positions: [{
    account_id: account.id, as_of: "2026-11-10", symbol: "VTI", name: "Total Stock Market", asset_class: "equity_etf", quantity, current_price: "100.00", market_value: "100.00", cost_basis: null, gain_loss: null, currency: "USD", is_stale: false, source_refreshed_at: account.source_refreshed_at, ...overrides,
  }] };
}
function renderPositions(list = accounts) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PositionsSection accounts={list} loading={false} error={false} /></QueryClientProvider>);
  return client;
}
beforeEach(() => {
  vi.mocked(getAccount).mockReset();
  vi.mocked(getAccount).mockImplementation(async (id) => ({ account: detail(accounts.find((account) => account.id === id)!, id === "Schwab" ? "1.000000000000000001" : "1.000000000000000002") }));
});
afterEach(cleanup);

describe("PositionsSection", () => {
  it("keeps the same symbol in separate accounts and filters by owner and symbol", async () => {
    renderPositions();
    const table = within(await screen.findByRole("region", { name: "Positions across accounts" }));
    await waitFor(() => expect(table.getAllByRole("row")).toHaveLength(3));
    expect(table.getAllByText("VTI")).toHaveLength(2);
    expect(table.getByText("Schwab Brokerage")).toBeTruthy();
    expect(table.getByText("Fidelity Brokerage")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Position account"), { target: { value: "Fidelity" } });
    expect(table.getAllByRole("row")).toHaveLength(2);
    expect(table.queryByText("Schwab Brokerage")).toBeNull();
    fireEvent.change(screen.getByLabelText("Search positions"), { target: { value: "missing" } });
    expect(screen.getByText("No positions match these filters.")).toBeTruthy();
  });

  it("sorts precise decimal quantities without converting to floating point", async () => {
    renderPositions();
    const table = within(await screen.findByRole("region", { name: "Positions across accounts" }));
    await waitFor(() => expect(table.getAllByRole("row")).toHaveLength(3));
    fireEvent.click(screen.getByRole("button", { name: "Sort positions by Quantity" }));
    expect(table.getAllByRole("row")[1].textContent).toContain("Schwab Brokerage");
    fireEvent.click(screen.getByRole("button", { name: "Sort positions by Quantity" }));
    expect(table.getAllByRole("row")[1].textContent).toContain("Fidelity Brokerage");
  });

  it("discloses missing account coverage while keeping available positions", async () => {
    vi.mocked(getAccount).mockImplementation(async (id) => {
      if (id === "Fidelity") throw new Error("Unavailable");
      return { account: detail(accounts[0], "1", { is_stale: true, market_value: null }) };
    });
    renderPositions();
    expect((await screen.findByRole("alert")).textContent).toContain("Fidelity Brokerage");
    expect(screen.getByText(/This account is missing from the table/)).toBeTruthy();
    expect(screen.getByText(/Stale saved data/)).toBeTruthy();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.getByRole("table")).toBeTruthy();
  });

  it("retains positions and marks them stale after a failed refetch", async () => {
    const client = renderPositions([accounts[0]]);
    await screen.findByRole("table");
    vi.mocked(getAccount).mockRejectedValue(new Error("Unavailable"));
    await client.invalidateQueries({ queryKey: ["accounts"] });
    expect((await screen.findByRole("alert")).textContent).toContain("Previously saved positions");
    expect(screen.getByText(/Stale saved data/)).toBeTruthy();
    expect(screen.getByRole("table")).toBeTruthy();
  });

  it("shows an honest empty state without accounts", async () => {
    renderPositions([]);
    expect(await screen.findByText(/No saved positions yet/)).toBeTruthy();
    expect(getAccount).not.toHaveBeenCalled();
  });
});
