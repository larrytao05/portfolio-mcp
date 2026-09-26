import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrderCancellationModal } from "./OrderCancellationModal";
import type { StoredOrder } from "../../api/client";
import * as client from "../../api/client";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof client>();
  return {
    ...actual,
    confirmOrderCancellation: vi.fn(),
    getOrder: vi.fn(),
  };
});

const sampleCancelableOrder: StoredOrder = {
  id: "ord-test-1",
  draft_id: "draft-test-1",
  fingerprint: "fp-test-1",
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
    quantity: "10",
    limit_price: "220.50",
  },
  state: "ACCEPTED",
  can_cancel: true,
  blocking_reason: null,
  broker_order_id: "brk-100",
  result: { code: null, message: null, source: null },
  fill: { quantity: "4", average_price: "220.50" },
  remaining_quantity: "6",
  provider_updated_at: "2026-09-25T12:00:00Z",
  provider_status_label: "PARTIALLY_FILLED",
  created_at: "2026-09-25T12:00:00Z",
  updated_at: "2026-09-25T12:01:00Z",
  version: 2,
};

function renderModal(props: Partial<Parameters<typeof OrderCancellationModal>[0]> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const defaultProps = {
    order: sampleCancelableOrder,
    isOpen: true,
    onClose: vi.fn(),
    onSuccess: vi.fn(),
    onReconcileRequested: vi.fn(),
    ...props,
  };
  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <OrderCancellationModal {...defaultProps} />
      </QueryClientProvider>,
    ),
    props: defaultProps,
  };
}

describe("OrderCancellationModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders order details, remaining shares, and undo warning", () => {
    renderModal();

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText(/Cancel Order/i)).toBeTruthy();
    expect(screen.getByText("Schwab Taxable ••••4821")).toBeTruthy();
    expect(screen.getByText("VTI")).toBeTruthy();
    expect(screen.getByText("4 @ $220.50")).toBeTruthy();
    expect(screen.getByText("6")).toBeTruthy();
    expect(
      screen.getByText(/Filled shares cannot be undone/i),
    ).toBeTruthy();
  });

  it("calls confirmOrderCancellation with expected version and state upon confirmation", async () => {
    vi.mocked(client.confirmOrderCancellation).mockResolvedValueOnce({
      order: {
        ...sampleCancelableOrder,
        state: "CANCELED",
        version: 3,
        can_cancel: false,
      },
    });

    const { props } = renderModal();

    const confirmButton = screen.getByRole("button", {
      name: /Confirm cancellation/i,
    });
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(client.confirmOrderCancellation).toHaveBeenCalledWith("ord-test-1", {
        expected_version: 2,
        expected_state: "ACCEPTED",
        confirmed: true,
      });
      expect(props.onSuccess).toHaveBeenCalled();
    });
  });

  it("disables buttons while cancellation mutation is pending to prevent double-click", async () => {
    let resolvePromise: (value: client.ConfirmOrderCancellationResponse) => void;
    const pendingPromise = new Promise<client.ConfirmOrderCancellationResponse>((resolve) => {
      resolvePromise = resolve;
    });
    vi.mocked(client.confirmOrderCancellation).mockReturnValueOnce(pendingPromise);

    renderModal();

    const confirmButton = screen.getByRole("button", {
      name: /Confirm cancellation/i,
    });
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(confirmButton.hasAttribute("disabled")).toBe(true);
      expect(
        screen.getByRole("button", { name: /Keep order/i }).hasAttribute("disabled"),
      ).toBe(true);
    });

    resolvePromise!({
      order: { ...sampleCancelableOrder, state: "CANCELED" },
    });
  });

  it("reloads order on 409 conflict and updates review state if order is still cancelable", async () => {
    const error = new client.ApiError(409, "Order conflict", "order_conflict");
    vi.mocked(client.confirmOrderCancellation).mockRejectedValueOnce(error);

    const updatedOrder: StoredOrder = {
      ...sampleCancelableOrder,
      version: 3,
      fill: { quantity: "8", average_price: "220.50" },
      remaining_quantity: "2",
      can_cancel: true,
    };
    vi.mocked(client.getOrder).mockResolvedValueOnce({ order: updatedOrder });

    renderModal();

    fireEvent.click(
      screen.getByRole("button", { name: /Confirm cancellation/i }),
    );

    await waitFor(() => {
      expect(client.getOrder).toHaveBeenCalledWith("ord-test-1");
      expect(screen.getByText(/Order state changed/i)).toBeTruthy();
      expect(screen.getByText("8 @ $220.50")).toBeTruthy();
      expect(screen.getByText("2")).toBeTruthy();
    });

    // Subsequent confirm uses the new version (3)
    vi.mocked(client.confirmOrderCancellation).mockResolvedValueOnce({
      order: { ...updatedOrder, state: "CANCELED" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Confirm cancellation/i }),
    );

    await waitFor(() => {
      expect(client.confirmOrderCancellation).toHaveBeenLastCalledWith(
        "ord-test-1",
        {
          expected_version: 3,
          expected_state: "ACCEPTED",
          confirmed: true,
        },
      );
    });
  });

  it("handles conflict when order becomes noncancelable (e.g. FILLED) by disabling cancellation", async () => {
    const error = new client.ApiError(409, "Order conflict", "order_conflict");
    vi.mocked(client.confirmOrderCancellation).mockRejectedValueOnce(error);

    const filledOrder: StoredOrder = {
      ...sampleCancelableOrder,
      version: 3,
      state: "FILLED",
      can_cancel: false,
      blocking_reason: "Order is already filled",
      fill: { quantity: "10", average_price: "220.50" },
      remaining_quantity: "0",
    };
    vi.mocked(client.getOrder).mockResolvedValueOnce({ order: filledOrder });

    renderModal();

    fireEvent.click(
      screen.getByRole("button", { name: /Confirm cancellation/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Order can no longer be canceled/i),
      ).toBeTruthy();
      expect(
        screen.queryByRole("button", { name: /Confirm cancellation/i }),
      ).toBeNull();
      expect(
        screen.getByRole("button", { name: "Close" }),
      ).toBeTruthy();
    });
  });

  it("removes cancellation action and offers reconciliation on UNKNOWN outcome", async () => {
    vi.mocked(client.confirmOrderCancellation).mockResolvedValueOnce({
      order: {
        ...sampleCancelableOrder,
        state: "UNKNOWN",
        can_cancel: false,
      },
    });

    const { props } = renderModal();

    fireEvent.click(
      screen.getByRole("button", { name: /Confirm cancellation/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Cancellation outcome is unknown/i),
      ).toBeTruthy();
      expect(
        screen.queryByRole("button", { name: /Confirm cancellation/i }),
      ).toBeNull();
      expect(
        screen.getByRole("button", { name: /Reconcile order/i }),
      ).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Reconcile order/i }));
    expect(props.onReconcileRequested).toHaveBeenCalledWith("ord-test-1");
  });

  it("handles conflict reload transitioning to UNKNOWN by offering reconciliation", async () => {
    const error = new client.ApiError(409, "Order conflict", "order_conflict");
    vi.mocked(client.confirmOrderCancellation).mockRejectedValueOnce(error);

    const unknownOrder: StoredOrder = {
      ...sampleCancelableOrder,
      version: 3,
      state: "UNKNOWN",
      can_cancel: false,
    };
    vi.mocked(client.getOrder).mockResolvedValueOnce({ order: unknownOrder });

    const { props } = renderModal();

    fireEvent.click(
      screen.getByRole("button", { name: /Confirm cancellation/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Cancellation outcome is unknown/i),
      ).toBeTruthy();
      expect(
        screen.getByRole("button", { name: /Reconcile order/i }),
      ).toBeTruthy();
      expect(
        screen.queryByRole("button", { name: /Confirm cancellation/i }),
      ).toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: /Reconcile order/i }));
    expect(props.onReconcileRequested).toHaveBeenCalledWith("ord-test-1");
  });

  it("closes modal on Escape key press", () => {
    const { props } = renderModal();

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(props.onClose).toHaveBeenCalled();
  });

  it("traps focus within the modal dialog on Tab press", () => {
    renderModal();

    const dialog = screen.getByRole("dialog");
    const closeBtn = screen.getByRole("button", { name: "Close dialog" });
    const confirmBtn = screen.getByRole("button", { name: /Confirm cancellation/i });

    // When focus is on the last button and Tab is pressed, it wraps to first
    confirmBtn.focus();
    expect(document.activeElement).toBe(confirmBtn);

    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(closeBtn);

    // When focus is on the first button and Shift+Tab is pressed, it wraps to last
    closeBtn.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirmBtn);
  });
});
