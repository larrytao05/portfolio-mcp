import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "../../api/client";
import { TradeSection } from "./TradingPanels";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows a saved unknown order and reconciles it without resubmitting", async () => {
  const order = { id: "local-order", draft_id: "draft", fingerprint: "hash", state: "UNKNOWN", instrument: { symbol: "VTI" }, result: { code: "unknown", message: null } };
  const list = vi.spyOn(api, "getUnresolvedOrders").mockResolvedValue({ orders: [order] });
  const reconcile = vi.spyOn(api, "reconcileOrder").mockResolvedValue({ order });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><TradeSection accounts={[{ id: "a", label: "Example account", provider: "Alpaca", account_type: "brokerage", currency: "USD", is_stale: false, source_refreshed_at: "2026-01-01" }]} /></QueryClientProvider>);
  expect(await screen.findByText(/VTI · UNKNOWN · local-order/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Reconcile order" }));
  await vi.waitFor(() => expect(reconcile).toHaveBeenCalledWith("local-order"));
  expect(list).toHaveBeenCalledWith("a");
});

it("reports an unavailable saved-order list", async () => {
  vi.spyOn(api, "getUnresolvedOrders").mockRejectedValue(new Error("offline"));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><TradeSection accounts={[{ id: "a", label: "Example account", provider: "Alpaca", account_type: "brokerage", currency: "USD", is_stale: false, source_refreshed_at: "2026-01-01" }]} /></QueryClientProvider>);
  expect((await screen.findByRole("alert")).textContent).toContain("Unresolved orders are unavailable");
});
