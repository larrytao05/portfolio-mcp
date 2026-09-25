import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrdersSection } from "./OrdersSection";
import type { Account, OrderListPage, OrderAuditPage, StoredOrder } from "../../api/client";

const api = vi.hoisted(() => ({
  getOrders: vi.fn(),
  getOrderAudit: vi.fn(),
  refreshOrder: vi.fn(),
}));

vi.mock("../../api/client", () => api);

function renderOrdersSection(accounts: Account[] = [sampleAccount]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OrdersSection accounts={accounts} />
    </QueryClientProvider>,
  );
}

const sampleAccount: Account = {
  id: "schwab-taxable-demo",
  provider: "schwab",
  label: "Schwab Taxable ••••4821",
  account_type: "taxable_brokerage",
  currency: "USD",
  is_stale: false,
  source_refreshed_at: "2026-09-12T20:00:00Z",
};

const sampleOrder: StoredOrder = {
  id: "ord-1",
  draft_id: "draft-1",
  fingerprint: "fp-1",
  account: {
    id: "schwab-taxable-demo",
    label: "Schwab Taxable ••••4821",
  },
  provider: "schwab",
  instrument: {
    id: "us-etf:VTI",
    symbol: "VTI",
  },
  instruction: {
    side: "buy",
    type: "limit",
    quantity: "1",
    limit_price: "300.25",
  },
  state: "ACCEPTED",
  broker_order_id: "brk-1",
  result: {
    code: null,
    message: null,
    source: null,
  },
  fill: null,
  provider_updated_at: "2026-09-12T20:00:00Z",
  provider_status_label: "OPEN",
  created_at: "2026-09-12T20:00:00Z",
  updated_at: "2026-09-12T20:00:00Z",
  version: 1,
  reconciliation: {
    status: "pending",
    source: null,
    provider_updated_at: null,
    next_refresh_at: "2026-09-12T20:00:30Z",
    target_order_id: "ord-1",
  },
};

describe("OrdersSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.location.hash = "#orders";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("does not poll or refresh when route is not #orders", async () => {
    vi.useFakeTimers();
    window.location.hash = "#accounts";

    const page: OrderListPage = {
      orders: [sampleOrder],
      next_cursor: null,
      refresh_groups: [
        {
          provider: "schwab",
          account_id: "schwab-taxable-demo",
          target_order_id: "ord-1",
          next_refresh_at: "2026-09-12T20:00:05Z",
        },
      ],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);

    renderOrdersSection();

    // Advance time by 10 seconds
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    expect(api.refreshOrder).not.toHaveBeenCalled();
  });

  it("schedules refresh when #orders is active and document is visible", async () => {
    vi.useFakeTimers();
    window.location.hash = "#orders";

    const page: OrderListPage = {
      orders: [sampleOrder],
      next_cursor: null,
      refresh_groups: [
        {
          provider: "schwab",
          account_id: "schwab-taxable-demo",
          target_order_id: "ord-1",
          next_refresh_at: "2026-09-12T20:00:05Z",
        },
      ],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);
    api.refreshOrder.mockResolvedValue({
      order: sampleOrder,
      refresh: {
        status: "attempted",
        provider_read_started: true,
        next_refresh_at: "2026-09-12T20:00:35Z",
        target_order_id: "ord-1",
        server_time: "2026-09-12T20:00:05Z",
      },
    });

    renderOrdersSection();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    expect(screen.getByText("VTI")).toBeTruthy();

    // Advance timer past the 5 second server delay
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });

    expect(api.refreshOrder).toHaveBeenCalledWith("ord-1", "scheduled");
  });

  it("stops scheduled refresh when document becomes hidden", async () => {
    vi.useFakeTimers();
    window.location.hash = "#orders";

    const page: OrderListPage = {
      orders: [sampleOrder],
      next_cursor: null,
      refresh_groups: [
        {
          provider: "schwab",
          account_id: "schwab-taxable-demo",
          target_order_id: "ord-1",
          next_refresh_at: "2026-09-12T20:00:05Z",
        },
      ],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);

    renderOrdersSection();

    // Switch document visibility to hidden before timer fires
    act(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => "hidden",
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    expect(api.refreshOrder).not.toHaveBeenCalled();
  });

  it("stops polling when all displayed orders are terminal", async () => {
    vi.useFakeTimers();
    window.location.hash = "#orders";

    const terminalOrder: StoredOrder = {
      ...sampleOrder,
      state: "FILLED",
      fill: {
        quantity: "1",
        average_price: "300.25",
      },
    };
    const page: OrderListPage = {
      orders: [terminalOrder],
      next_cursor: null,
      refresh_groups: [],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);

    renderOrdersSection();

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    expect(api.refreshOrder).not.toHaveBeenCalled();
  });

  it("polls saved-list GET when SUBMITTING order is present without syncable target", async () => {
    vi.useFakeTimers();
    window.location.hash = "#orders";

    const submittingOrder: StoredOrder = {
      ...sampleOrder,
      state: "SUBMITTING",
    };
    const page: OrderListPage = {
      orders: [submittingOrder],
      next_cursor: null,
      refresh_groups: [],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);

    renderOrdersSection();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    expect(screen.getByText("SUBMITTING")).toBeTruthy();

    // Fast-forward saved list poll delay (2 seconds)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(api.getOrders).toHaveBeenCalledTimes(2);
    expect(api.refreshOrder).not.toHaveBeenCalled();
  });

  it("explains off-page scheduled refresh target in the UI", async () => {
    const page: OrderListPage = {
      orders: [sampleOrder],
      next_cursor: null,
      refresh_groups: [
        {
          provider: "schwab",
          account_id: "schwab-taxable-demo",
          target_order_id: "off-page-order-99",
          next_refresh_at: "2026-09-12T20:00:30Z",
        },
      ],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);

    renderOrdersSection();

    expect(
      await screen.findByText(/off-page in this account group/i),
    ).toBeTruthy();
    expect(screen.getByText(/off-page-order-99/)).toBeTruthy();
  });

  it("renders UNKNOWN order with Reconcile and Audit actions and NO resubmit action", async () => {
    const unknownOrder: StoredOrder = {
      ...sampleOrder,
      state: "UNKNOWN",
    };
    const page: OrderListPage = {
      orders: [unknownOrder],
      next_cursor: null,
      refresh_groups: [],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);

    renderOrdersSection();

    expect(await screen.findByText("UNKNOWN")).toBeTruthy();
    expect(screen.getByRole("button", { name: /reconcile/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /audit/i })).toBeTruthy();

    expect(screen.queryByRole("button", { name: /resubmit/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /submit again/i })).toBeNull();
  });

  it("handles throttled manual refresh response and displays gate message", async () => {
    const page: OrderListPage = {
      orders: [sampleOrder],
      next_cursor: null,
      refresh_groups: [],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);
    api.refreshOrder.mockResolvedValue({
      order: sampleOrder,
      refresh: {
        status: "throttled",
        provider_read_started: false,
        next_refresh_at: "2026-09-12T20:01:00Z",
        target_order_id: "ord-1",
        server_time: "2026-09-12T20:00:15Z",
      },
    });

    renderOrdersSection();

    const refreshBtn = await screen.findByRole("button", { name: /refresh/i });
    fireEvent.click(refreshBtn);

    expect(await screen.findByText(/throttled/i)).toBeTruthy();
  });

  it("opens audit history panel and closes it", async () => {
    const page: OrderListPage = {
      orders: [sampleOrder],
      next_cursor: null,
      refresh_groups: [],
      server_time: "2026-09-12T20:00:00Z",
    };
    const audit: OrderAuditPage = {
      events: [
        {
          event_id: "evt-1",
          draft_id: "draft-1",
          order_id: "ord-1",
          account_id: "schwab-taxable-demo",
          event_type: "status_transition",
          actor: "system",
          previous_state: "SUBMITTING",
          next_state: "ACCEPTED",
          code: null,
          details: { reason: "Broker accepted" },
          occurred_at: "2026-09-12T20:00:01Z",
        },
      ],
      next_cursor: null,
    };
    api.getOrders.mockResolvedValue(page);
    api.getOrderAudit.mockResolvedValue(audit);

    renderOrdersSection();

    const auditBtn = await screen.findByRole("button", { name: /audit/i });
    fireEvent.click(auditBtn);

    expect(await screen.findByText(/Order Audit/i)).toBeTruthy();
    expect(await screen.findByText("status_transition")).toBeTruthy();
    expect(screen.getByText(/SUBMITTING → ACCEPTED/)).toBeTruthy();

    const closeBtn = screen.getByRole("button", { name: /close audit/i });
    fireEvent.click(closeBtn);

    await waitFor(() => {
      expect(screen.queryByText("status_transition")).toBeNull();
    });
  });

  it("picks earliest refresh group across multiple accounts to prevent starvation", async () => {
    vi.useFakeTimers();
    window.location.hash = "#orders";

    const order1: StoredOrder = { ...sampleOrder, id: "ord-1" };
    const order2: StoredOrder = {
      ...sampleOrder,
      id: "ord-2",
      account: { id: "fidelity-roth-demo", label: "Fidelity Roth" },
    };

    const page: OrderListPage = {
      orders: [order1, order2],
      next_cursor: null,
      refresh_groups: [
        {
          provider: "schwab",
          account_id: "schwab-taxable-demo",
          target_order_id: "ord-1",
          next_refresh_at: "2026-09-12T20:00:30Z",
        },
        {
          provider: "fidelity",
          account_id: "fidelity-roth-demo",
          target_order_id: "ord-2",
          next_refresh_at: "2026-09-12T20:00:05Z",
        },
      ],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);
    api.refreshOrder.mockResolvedValue({
      order: order2,
      refresh: {
        status: "attempted",
        provider_read_started: true,
        next_refresh_at: "2026-09-12T20:00:35Z",
        target_order_id: "ord-2",
        server_time: "2026-09-12T20:00:05Z",
      },
    });

    renderOrdersSection();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(api.refreshOrder).toHaveBeenCalledWith("ord-2", "scheduled");
  });

  it("submitting order triggers 2s refetch even when nonterminal order with 30s schedule coexists", async () => {
    vi.useFakeTimers();
    window.location.hash = "#orders";

    const submittingOrder: StoredOrder = {
      ...sampleOrder,
      id: "ord-sub",
      state: "SUBMITTING",
    };
    const acceptedOrder: StoredOrder = {
      ...sampleOrder,
      id: "ord-acc",
      state: "ACCEPTED",
    };

    const page: OrderListPage = {
      orders: [submittingOrder, acceptedOrder],
      next_cursor: null,
      refresh_groups: [
        {
          provider: "schwab",
          account_id: "schwab-taxable-demo",
          target_order_id: "ord-acc",
          next_refresh_at: "2026-09-12T20:00:30Z",
        },
      ],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);

    renderOrdersSection();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    expect(api.getOrders).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(api.getOrders).toHaveBeenCalledTimes(2);
    expect(api.refreshOrder).not.toHaveBeenCalled();
  });

  it("reconciliation Next indicator only renders on the designated target order", async () => {
    const targetOrder: StoredOrder = {
      ...sampleOrder,
      id: "ord-target",
      reconciliation: {
        status: "pending",
        source: null,
        provider_updated_at: null,
        next_refresh_at: "2026-09-12T20:00:30Z",
        target_order_id: "ord-target",
      },
    };
    const nonTargetOrder: StoredOrder = {
      ...sampleOrder,
      id: "ord-nontarget",
      reconciliation: {
        status: "pending",
        source: null,
        provider_updated_at: null,
        next_refresh_at: "2026-09-12T20:00:30Z",
        target_order_id: "ord-target",
      },
    };

    const page: OrderListPage = {
      orders: [targetOrder, nonTargetOrder],
      next_cursor: null,
      refresh_groups: [],
      server_time: "2026-09-12T20:00:00Z",
    };
    api.getOrders.mockResolvedValue(page);

    renderOrdersSection();

    const matches = await screen.findAllByText(/Next:/);
    expect(matches).toHaveLength(1);
  });
});
