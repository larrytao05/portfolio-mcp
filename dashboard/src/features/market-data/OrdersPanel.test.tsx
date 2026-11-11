import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OrdersPanel, type DisplayOrder, type DisplayOrderHistory } from "./OrdersPanel";

afterEach(cleanup);
const order: DisplayOrder = { id: "123", account_id: "a", leg_id: "1", client_order_id: null, symbol: "VTI", side: "BUY", order_type: "LIMIT", quantity: "2.0001", filled_quantity: "0.5", filled_avg_price: "10.1234", limit_price: null, status: "WORKING", time_placed: null, time_executed: null };
const history: DisplayOrderHistory = { coverage: "open_and_executed", source: "Fixture", freshness: "current", warning: "Fictional data.", synced_at: null, lookback_days: 90, truncated: false, orders: [order] };

describe("order panel", () => {
  it("preserves separate legs and shows truncation and stale state", () => {
    render(<OrdersPanel accountLabel="A" symbol="VTI" view="open" history={{ ...history, truncated: true, orders: [order, { ...order, leg_id: "2", status: "CANCELED" }] }} failed />);
    expect(screen.getAllByText("123")).toHaveLength(2);
    expect(screen.getByText("CANCELED")).toBeTruthy();
    expect(screen.getByText(/More orders may be missing/)).toBeTruthy();
    expect(screen.getByText(/Previously loaded orders are stale/)).toBeTruthy();
  });
  it("does not call unsupported open orders empty", () => {
    render(<OrdersPanel accountLabel="A" symbol="VTI" view="open" history={{ ...history, coverage: "executed_only", orders: [] }} failed={false} />);
    expect(screen.getByText(/Open orders are unavailable/)).toBeTruthy();
    expect(screen.queryByText(/No open/)).toBeNull();
  });
  it("paginates an account's results", () => {
    render(<OrdersPanel accountLabel="A" symbol="VTI" view="closed" history={{ ...history, orders: Array.from({ length: 21 }, (_, index) => ({ ...order, id: `order-${index}` })) }} failed={false} />);
    expect(screen.getByText("order-0")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("order-20")).toBeTruthy();
    expect(screen.queryByText("order-0")).toBeNull();
  });
});

it("requires inline confirmation before cancellation and reconciles unknown", async () => {
  const api = await import("../../api/client");
  const cancel = vi.spyOn(api, "cancelBrokerOrder").mockResolvedValue({ state: "UNKNOWN" });
  const reconcile = vi.spyOn(api, "reconcileCancellation").mockResolvedValue({ state: "FILLED" });
  render(<OrdersPanel accountLabel="Alpaca" symbol="VTI" view="open" history={{ ...history, source: "Alpaca", orders: [{ ...order, status: "new", leg_id: null }] }} failed={false} />);
  fireEvent.click(screen.getByRole("button", { name: "Review cancellation" }));
  expect(screen.getByText(/LIVE Alpaca order/)).toBeTruthy();
  expect(cancel).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm cancellation" }));
  await screen.findByText(/Cancellation state: UNKNOWN/);
  expect(cancel).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "Confirm cancellation" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reconcile cancellation" }));
  await screen.findByText(/Cancellation state: FILLED/);
  expect(reconcile).toHaveBeenCalledOnce();
  cancel.mockRestore();
  reconcile.mockRestore();
});

it("does not label a refused cancellation as sent", async () => {
  const api = await import("../../api/client");
  const cancel = vi.spyOn(api, "cancelBrokerOrder").mockRejectedValue(new api.ApiError("Order unavailable", 400));
  render(<OrdersPanel accountLabel="Alpaca" symbol="VTI" view="open" history={{ ...history, source: "Alpaca", orders: [{ ...order, status: "new", leg_id: null }] }} failed={false} />);
  fireEvent.click(screen.getByRole("button", { name: "Review cancellation" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm cancellation" }));
  await screen.findByText(/Cancellation state: NOT_CONFIRMED/);
  expect(screen.queryByText(/Cancellation state: UNKNOWN/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Reconcile cancellation" })).toBeNull();
  cancel.mockRestore();
});
