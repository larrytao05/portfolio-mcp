import { cleanup, screen, act, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderOrdersSection, sampleOrder } from "./ordersTestSupport";
import type { OrderListPage } from "../../api/client";

const api = vi.hoisted(() => ({
  getOrders: vi.fn(),
  getOrderAudit: vi.fn(),
  refreshOrder: vi.fn(),
  confirmOrderCancellation: vi.fn(),
  getOrder: vi.fn(),
  getCancellationRequest: vi.fn(),
  createCancellationMcpAuthorization: vi.fn(),
}));

vi.mock("../../api/client", () => api);

describe("OrdersSection refresh recovery", () => {
  const idlePage: OrderListPage = {
    orders: [sampleOrder],
    next_cursor: null,
    refresh_groups: [],
    server_time: "2026-09-12T20:00:00Z",
  };
  const duePage: OrderListPage = {
    ...idlePage,
    refresh_groups: [
      {
        provider: "schwab", account_id: "a", target_order_id: "ord-a",
        next_refresh_at: "2026-09-12T20:00:00Z",
      },
      {
        provider: "schwab", account_id: "b", target_order_id: "ord-b",
        next_refresh_at: "2026-09-12T20:00:00Z",
      },
    ],
  };
  const refreshed = {
    order: sampleOrder,
    refresh: { status: "attempted", provider_read_started: true },
  };

  async function advance(ms: number) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  }

  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] });
    window.location.hash = "#orders";
    Object.defineProperty(document, "visibilityState", {
      configurable: true, get: () => "visible",
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("reloads after a failed refresh and lets another account progress before retrying", async () => {
    api.getOrders.mockResolvedValueOnce(duePage).mockResolvedValueOnce(duePage)
      .mockResolvedValue(idlePage);
    api.refreshOrder.mockRejectedValueOnce(new Error("temporary transport failure"))
      .mockResolvedValue(refreshed);
    renderOrdersSection();
    await advance(2100);
    expect(api.refreshOrder.mock.calls).toEqual([
      ["ord-a", "scheduled"], ["ord-b", "scheduled"],
    ]);
    expect(api.getOrders).toHaveBeenCalledTimes(3);
  });

  it("retries a failed account after reloading its authoritative target", async () => {
    const firstPage = { ...duePage, refresh_groups: duePage.refresh_groups.slice(0, 1) };
    const newTarget = {
      ...firstPage,
      refresh_groups: firstPage.refresh_groups.map((group) => ({
        ...group, target_order_id: "ord-a-new",
      })),
    };
    api.getOrders.mockResolvedValueOnce(firstPage).mockResolvedValueOnce(newTarget)
      .mockResolvedValue(idlePage);
    api.refreshOrder.mockRejectedValueOnce(new Error("temporary transport failure"))
      .mockResolvedValue(refreshed);
    renderOrdersSection();
    await advance(1100);
    expect(api.getOrders).toHaveBeenCalledTimes(2);
    expect(api.refreshOrder).toHaveBeenCalledTimes(1);
    await advance(2000);
    expect(api.refreshOrder.mock.calls).toEqual([
      ["ord-a", "scheduled"], ["ord-a-new", "scheduled"],
    ]);
  });

  it("recovers a failed plan reload before observing another order", async () => {
    api.getOrders.mockResolvedValueOnce(duePage)
      .mockRejectedValueOnce(new Error("list endpoint unavailable"))
      .mockResolvedValueOnce({ ...duePage, refresh_groups: duePage.refresh_groups.slice(1) })
      .mockResolvedValue(idlePage);
    api.refreshOrder.mockResolvedValue(refreshed);
    renderOrdersSection();
    await advance(1100);
    expect(api.getOrders).toHaveBeenCalledTimes(2);
    expect(api.refreshOrder).toHaveBeenCalledTimes(1);
    await advance(1800);
    expect(api.refreshOrder).toHaveBeenCalledTimes(1);
    await advance(1300);
    expect(api.refreshOrder.mock.calls).toEqual([
      ["ord-a", "scheduled"], ["ord-b", "scheduled"],
    ]);
  });

  it("retries an unavailable activation plan", async () => {
    api.getOrders.mockRejectedValueOnce(new Error("list endpoint unavailable"))
      .mockResolvedValueOnce({ ...duePage, refresh_groups: duePage.refresh_groups.slice(1) })
      .mockResolvedValue(idlePage);
    api.refreshOrder.mockResolvedValue(refreshed);
    renderOrdersSection();
    await advance(3100);
    expect(api.refreshOrder).toHaveBeenCalledWith("ord-b", "scheduled");
    expect(api.getOrders).toHaveBeenCalledTimes(3);
  });

  it("keeps reading unchanged SUBMITTING data after a saved-list failure", async () => {
    const submitting = { ...idlePage, orders: [{ ...sampleOrder, state: "SUBMITTING" }] };
    api.getOrders.mockResolvedValueOnce(submitting)
      .mockRejectedValueOnce(new Error("temporary list failure"))
      .mockResolvedValueOnce(submitting).mockResolvedValueOnce(submitting)
      .mockResolvedValue(idlePage);
    renderOrdersSection();
    await advance(8100);
    expect(api.getOrders).toHaveBeenCalledTimes(5);
    expect(api.refreshOrder).not.toHaveBeenCalled();
  });

  it("loads a new plan on return instead of restarting a cached relative deadline", async () => {
    const cached = {
      ...duePage,
      refresh_groups: [{ ...duePage.refresh_groups[0], next_refresh_at: "2026-09-12T20:00:05Z" }],
    };
    api.getOrders.mockResolvedValueOnce(cached).mockResolvedValueOnce(duePage)
      .mockResolvedValue(idlePage);
    api.refreshOrder.mockResolvedValue(refreshed);
    renderOrdersSection();
    await advance(1100);
    act(() => {
      window.location.hash = "#overview";
      window.dispatchEvent(new Event("hashchange"));
    });
    await advance(10000);
    expect(api.refreshOrder).not.toHaveBeenCalled();
    act(() => {
      window.location.hash = "#orders";
      window.dispatchEvent(new Event("hashchange"));
    });
    await advance(1100);
    expect(api.getOrders).toHaveBeenCalledTimes(3);
    expect(api.refreshOrder).toHaveBeenCalledWith("ord-a", "scheduled");
  });

  it("discards a late refresh response after the Orders view is hidden", async () => {
    let finishRefresh: ((value: typeof refreshed) => void) | undefined;
    api.getOrders.mockResolvedValue(duePage);
    api.refreshOrder.mockImplementation(() => new Promise((resolve) => {
      finishRefresh = resolve;
    }));
    renderOrdersSection();
    await advance(1100);
    expect(api.refreshOrder).toHaveBeenCalledTimes(1);
    act(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true, get: () => "hidden",
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => {
      finishRefresh?.({ ...refreshed, refresh: { status: "throttled", provider_read_started: false } });
    });
    await advance(10000);
    expect(api.getOrders).toHaveBeenCalledTimes(1);
    expect(api.refreshOrder).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Refresh throttled/)).toBeNull();
  });

  it("discards a late failed refresh after the Orders view is hidden", async () => {
    let failRefresh: ((reason: Error) => void) | undefined;
    api.getOrders.mockResolvedValue(duePage);
    api.refreshOrder.mockImplementation(() => new Promise((_resolve, reject) => {
      failRefresh = reject;
    }));
    renderOrdersSection();
    await advance(1100);
    act(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true, get: () => "hidden",
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => { failRefresh?.(new Error("late transport failure")); });
    await advance(10000);
    expect(api.getOrders).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Order refresh failed/)).toBeNull();
  });


  it("wakes an idle scheduler when saved-orders invalidation adds targets", async () => {
    const newPlan = { ...duePage, refresh_groups: duePage.refresh_groups.slice(1) };
    api.getOrders.mockResolvedValueOnce(idlePage).mockResolvedValueOnce(newPlan)
      .mockResolvedValueOnce(newPlan).mockResolvedValue(idlePage);
    api.refreshOrder.mockResolvedValue(refreshed);
    const { queryClient } = renderOrdersSection();
    await advance(100);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
    });
    await advance(1100);
    expect(api.refreshOrder).toHaveBeenCalledWith("ord-b", "scheduled");
    expect(api.getOrders).toHaveBeenCalledTimes(4);
  });

  it("uses a nearer authoritative plan after external saved-orders invalidation", async () => {
    const delayed = {
      ...duePage,
      refresh_groups: [{ ...duePage.refresh_groups[0], next_refresh_at: "2026-09-12T20:00:30Z" }],
    };
    const newPlan = { ...duePage, refresh_groups: duePage.refresh_groups.slice(1) };
    api.getOrders.mockResolvedValueOnce(delayed).mockResolvedValueOnce(newPlan)
      .mockResolvedValueOnce(newPlan).mockResolvedValue(idlePage);
    api.refreshOrder.mockResolvedValue(refreshed);
    const { queryClient } = renderOrdersSection();
    await advance(100);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
    });
    await advance(1100);
    expect(api.refreshOrder.mock.calls).toEqual([["ord-b", "scheduled"]]);
  });


  it("caps repeated transport retries at thirty seconds", async () => {
    const firstPage = { ...duePage, refresh_groups: duePage.refresh_groups.slice(0, 1) };
    const attempts: number[] = [];
    api.getOrders.mockResolvedValue(firstPage);
    api.refreshOrder.mockImplementation(() => {
      attempts.push(performance.now());
      return Promise.reject(new Error("transport unavailable"));
    });
    renderOrdersSection();
    await advance(92000);
    expect(attempts).toEqual([1000, 3000, 7000, 15000, 31000, 61000, 91000]);
  });

  it("waits for a scheduled refresh and keeps manual refresh disabled", async () => {
    let finishRefresh: ((value: typeof refreshed) => void) | undefined;
    api.getOrders.mockResolvedValueOnce(duePage).mockResolvedValue(idlePage);
    api.refreshOrder.mockImplementation(() => new Promise((resolve) => {
      finishRefresh = resolve;
    }));
    renderOrdersSection();
    await advance(31000);
    expect(api.refreshOrder).toHaveBeenCalledTimes(1);
    expect(api.getOrders).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Refresh" }).hasAttribute("disabled")).toBe(true);
    await act(async () => { finishRefresh?.(refreshed); });
    await advance(100);
    expect(api.getOrders).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Refresh" }).hasAttribute("disabled")).toBe(false);
  });

  it("ignores a late activation plan when the view becomes hidden", async () => {
    let finishPlan: ((page: OrderListPage) => void) | undefined;
    api.getOrders.mockImplementation(() => new Promise((resolve) => {
      finishPlan = resolve;
    }));
    renderOrdersSection();
    await advance(100);
    act(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true, get: () => "hidden",
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => { finishPlan?.(duePage); });
    await advance(10000);
    expect(api.getOrders).toHaveBeenCalledTimes(1);
    expect(api.refreshOrder).not.toHaveBeenCalled();
    expect(screen.queryByText(/Order refresh failed/)).toBeNull();
  });


  it("clears the list-error display when a retried plan succeeds with no pending work", async () => {
    api.getOrders.mockRejectedValueOnce(new Error("list endpoint unavailable"))
      .mockResolvedValue(idlePage);
    renderOrdersSection();
    await advance(100);
    expect(screen.getByText("Unable to load orders.")).toBeTruthy();
    await advance(2100);
    expect(api.getOrders).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Unable to load orders.")).toBeNull();
    expect(screen.queryByText(/Retrying shortly/)).toBeNull();
  });

  it("keeps scheduled work and manual controls blocked behind a pending manual refresh", async () => {
    const laterPage = {
      ...duePage,
      refresh_groups: [{ ...duePage.refresh_groups[0], target_order_id: sampleOrder.id,
        next_refresh_at: "2026-09-12T20:00:05Z" }],
    };
    let finish: () => void = () => { throw new Error("Manual refresh has not started"); };
    api.getOrders.mockResolvedValueOnce(laterPage).mockResolvedValue(idlePage);
    api.refreshOrder.mockImplementationOnce(() => new Promise((resolve) => {
      finish = () => resolve(refreshed);
    })).mockResolvedValue({ ...refreshed, refresh: { status: "throttled", provider_read_started: false } });
    renderOrdersSection();
    await advance(100);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await advance(5100);
    expect(api.refreshOrder.mock.calls).toEqual([[sampleOrder.id, "manual"]]);
    expect(screen.getByRole("button", { name: "Refresh" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.refreshOrder).toHaveBeenCalledTimes(1);
    await act(async () => { finish(); });
    await advance(1000);
    expect(api.refreshOrder).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Refresh" }).hasAttribute("disabled")).toBe(false);
  });

  it.each(["success", "failure"])("waits for the old activation's pending request after reactivation and %s", async (outcome) => {
    const firstPage = { ...duePage, refresh_groups: duePage.refresh_groups.slice(0, 1) };
    const nextPage = { ...duePage, refresh_groups: duePage.refresh_groups.slice(1) };
    let finish: () => void = () => { throw new Error("Scheduled refresh has not started"); };
    api.getOrders.mockResolvedValueOnce(firstPage).mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce(nextPage).mockResolvedValue(idlePage);
    api.refreshOrder.mockImplementationOnce(() => new Promise((resolve, reject) => {
      finish = () => outcome === "success" ? resolve(refreshed) : reject(new Error("Old observation failed"));
    })).mockResolvedValue(refreshed);
    renderOrdersSection();
    await advance(1100);
    act(() => { window.location.hash = "#overview"; window.dispatchEvent(new Event("hashchange")); });
    await advance(100);
    act(() => { window.location.hash = "#orders"; window.dispatchEvent(new Event("hashchange")); });
    await advance(1100);
    expect(api.refreshOrder.mock.calls).toEqual([["ord-a", "scheduled"]]);
    await act(async () => { finish(); });
    await advance(1100);
    expect(api.refreshOrder.mock.calls).toEqual([["ord-a", "scheduled"], ["ord-b", "scheduled"]]);
    expect(screen.queryByText(/Order refresh failed/)).toBeNull();
  });

  it("defers modal reconciliation visibly until another order refresh settles", async () => {
    const cancelable = { ...sampleOrder, id: "cancel-b", can_cancel: true };
    const unknown = { ...cancelable, state: "UNKNOWN", can_cancel: false };
    const page = { ...idlePage, orders: [sampleOrder, cancelable] };
    api.getOrders.mockResolvedValueOnce(page).mockResolvedValue({ ...page, orders: [sampleOrder, unknown] });
    api.confirmOrderCancellation.mockResolvedValue({ order: unknown });
    let finish: () => void = () => { throw new Error("Manual refresh has not started"); };
    api.refreshOrder.mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve(refreshed); })).mockResolvedValue(refreshed);
    renderOrdersSection();
    await advance(100);
    fireEvent.click(screen.getAllByRole("button", { name: "Refresh" })[0]);
    await advance(100);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancellation" }));
    await advance(100);
    expect(screen.getByRole("button", { name: "Reconcile order" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Reconcile order" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(api.refreshOrder.mock.calls).toEqual([[sampleOrder.id, "manual"]]);
    await act(async () => { finish(); });
    await advance(100);
    expect(screen.getByRole("button", { name: "Reconcile order" }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Reconcile order" }));
    await advance(100);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.refreshOrder.mock.calls).toEqual([[sampleOrder.id, "manual"], ["cancel-b", "manual"]]);
  });

});
