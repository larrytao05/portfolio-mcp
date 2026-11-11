import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IntradayChart } from "./IntradayChart";
import { TickerAccounts } from "./TickerAccounts";
import type { Account } from "../../api/client";

const api = vi.hoisted(() => ({ getBrokerOrders: vi.fn(), getAccount: vi.fn() }));
vi.mock("../../api/client", () => api);
afterEach(cleanup);
const account: Account = { id: "one", label: "Fidelity fixture", provider: "Fidelity", account_type: "roth_ira", currency: "USD", is_stale: false, source_refreshed_at: "2026-11-10T15:00:00Z" };

describe("ticker workspace", () => {
  it("inspects exact candle values through an accessible slider", () => {
    render(<IntradayChart history={{ session_date: "2026-11-10", source: "fixture", feed: "fixture", delay_seconds: 900, extended_hours: false, available_through: "2026-11-10T15:00:00Z", bars: [
      { time: "2026-11-10T14:30:00Z", open: "100.00001", high: "102", low: "99", close: "101", volume: "500" },
      { time: "2026-11-10T14:31:00Z", open: "101", high: "103", low: "100", close: "102", volume: "600" },
    ] }} />);
    expect(screen.getByRole("img", { name: "Current-day price candles and volume" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Inspect price candle"), { target: { value: "0" } });
    expect(screen.getByText(/100.00001/)).toBeTruthy();
  });

  it("discloses executed-only coverage even with no orders", async () => {
    api.getBrokerOrders.mockResolvedValue({ history: { account, coverage: "executed_only", freshness: "daily", warning: "This connection does not expose open orders.", synced_at: null, lookback_days: 90, orders: [] } });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TickerAccounts accounts={[account]} symbol="NVDA" /></QueryClientProvider>);
    expect(await screen.findByText(/This connection does not expose open orders/)).toBeTruthy();
    expect(screen.getByText(/Open orders are unavailable/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText(/No historical NVDA orders returned/)).toBeTruthy();
  });

  it("shows an order failure without claiming no orders", async () => {
    api.getBrokerOrders.mockRejectedValue(new Error("synthetic failure"));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TickerAccounts accounts={[account]} symbol="NVDA" /></QueryClientProvider>);
    expect(await screen.findByText(/Brokerage order history is unavailable/)).toBeTruthy();
    expect(screen.queryByText(/No executed NVDA orders returned/)).toBeNull();
  });

  it("renders the saved position timestamp from account details", async () => {
    api.getAccount.mockResolvedValue({ account: { ...account, source_refreshed_at: undefined, refreshed_at: "2026-11-10T15:00:00Z", positions: [] } });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TickerAccounts accounts={[account]} symbol="NVDA" /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Your positions" }));
    expect(await screen.findByText(/No saved position in NVDA/)).toBeTruthy();
    expect(screen.queryByText(/Invalid Date/)).toBeNull();
    expect(screen.getByText(/Saved 11\/10\/2026/)).toBeTruthy();
  });
});
