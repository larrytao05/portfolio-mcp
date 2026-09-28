import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OwnerReview } from "./OwnerReview";
import type { OrderDraft } from "../api/client";
import * as client from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof client>();
  return {
    ...actual,
    getOrderDraft: vi.fn(),
    issueMcpAuthorization: vi.fn(),
    getCancellationRequest: vi.fn(),
    createCancellationMcpAuthorization: vi.fn(),
  };
});

const sampleDraft: OrderDraft = {
  id: "draft-mcp-1",
  account: {
    id: "schwab-taxable-demo",
    label: "Schwab Taxable ••••4821",
    provider: "schwab",
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
    quantity: "5",
    limit_price: "220.00",
    time_in_force: "day",
  },
  quote: {
    observed_at: "2026-09-25T20:00:00Z",
    last_price: "220.00",
    bid_price: "219.95",
    ask_price: "220.05",
    source: "fixture",
  },
  safety: {
    estimated_notional: "1100.00",
    account_refreshed_at: "2026-09-25T19:50:00Z",
    capability_observed_at: "2026-09-25T19:50:00Z",
    capability_last_success_at: "2026-09-25T19:50:00Z",
  },
  warnings: [],
  fingerprint: "fp-sample-123",
  created_at: "2026-09-25T20:00:00Z",
  expires_at: "2026-09-25T20:05:00Z",
};

const sampleCancellationRequest = {
  id: "cancel-req-1",
  order_id: "ord-456",
  expected_version: 2,
  expected_state: "ACCEPTED",
  account_id: "schwab-taxable-demo",
  provider: "schwab",
  symbol: "VTI",
  broker_order_id: "brk-789",
  remaining_quantity: "5",
  fingerprint: "fp-cancel-456",
  created_at: "2026-09-25T20:00:00Z",
  expires_at: "2026-09-25T20:05:00Z",
  status: "pending",
  invalidation_reason: null,
};

describe("OwnerReview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("loads saved draft with GET and does not issue a code on load", async () => {
    vi.mocked(client.getOrderDraft).mockResolvedValueOnce({ draft: sampleDraft });

    render(<OwnerReview action="submit" draftId="draft-mcp-1" onClose={vi.fn()} />);

    expect(screen.getByText(/Loading review details…/i)).toBeTruthy();

    await waitFor(() => {
      expect(client.getOrderDraft).toHaveBeenCalledWith("draft-mcp-1");
      expect(screen.getByText("Vanguard Total Stock Market ETF (VTI)")).toBeTruthy();
      expect(screen.getByText("fp-sample-123")).toBeTruthy();
      expect(screen.getByText("1100.00")).toBeTruthy();
    });

    // Issuance endpoint is never called on GET load
    expect(client.issueMcpAuthorization).not.toHaveBeenCalled();
  });

  it("disables Issue code button until confirmation checkbox is checked", async () => {
    vi.mocked(client.getOrderDraft).mockResolvedValueOnce({ draft: sampleDraft });

    render(<OwnerReview action="submit" draftId="draft-mcp-1" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("fp-sample-123")).toBeTruthy();
    });

    const issueButton = screen.getByRole("button", { name: "Issue code" });
    expect(issueButton.hasAttribute("disabled")).toBe(true);

    const checkbox = screen.getByRole("checkbox", {
      name: /I confirm this order instruction/i,
    });
    fireEvent.click(checkbox);

    expect(issueButton.hasAttribute("disabled")).toBe(false);
  });

  it("issues authorization code only upon explicit confirmed action", async () => {
    vi.mocked(client.getOrderDraft).mockResolvedValueOnce({ draft: sampleDraft });
    vi.mocked(client.issueMcpAuthorization).mockResolvedValueOnce({
      authorization_id: "auth-123",
      code: "87654321",
      expires_at: "2026-09-25T20:05:00Z",
      draft: {
        id: "draft-mcp-1",
        symbol: "VTI",
        side: "buy",
        quantity: "5",
        order_type: "limit",
        limit_price: "220.00",
        fingerprint: "fp-sample-123",
      },
    });

    render(<OwnerReview action="submit" draftId="draft-mcp-1" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("fp-sample-123")).toBeTruthy();
    });

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I confirm this order instruction/i,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Issue code" }));

    await waitFor(() => {
      expect(client.issueMcpAuthorization).toHaveBeenCalledWith(
        "draft-mcp-1",
        "fp-sample-123",
        true,
      );
      expect(screen.getByText("87654321")).toBeTruthy();
      expect(screen.getByText(/One-Time Authorization Code:/i)).toBeTruthy();
      expect(screen.getByRole("status")).toBeTruthy();
    });
  });

  it("displays error message if draft lookup fails", async () => {
    vi.mocked(client.getOrderDraft).mockRejectedValueOnce(
      new Error("Order draft not found"),
    );

    render(<OwnerReview action="submit" draftId="draft-missing" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
      expect(screen.getByText(/Order draft not found/i)).toBeTruthy();
    });
  });

  it("displays error message if code issuance fails", async () => {
    vi.mocked(client.getOrderDraft).mockResolvedValueOnce({ draft: sampleDraft });
    vi.mocked(client.issueMcpAuthorization).mockRejectedValueOnce(
      new Error("Order draft has expired"),
    );

    render(<OwnerReview action="submit" draftId="draft-mcp-1" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("fp-sample-123")).toBeTruthy();
    });

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I confirm this order instruction/i,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Issue code" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
      expect(screen.getByText(/Order draft has expired/i)).toBeTruthy();
    });
  });

  it("loads saved cancellation request with GET and displays version, state, remaining quantity, fingerprint", async () => {
    vi.mocked(client.getCancellationRequest).mockResolvedValueOnce({
      cancellation_request: sampleCancellationRequest,
    });

    render(<OwnerReview action="cancel" requestId="cancel-req-1" onClose={vi.fn()} />);

    expect(screen.getByText(/Loading review details…/i)).toBeTruthy();

    await waitFor(() => {
      expect(client.getCancellationRequest).toHaveBeenCalledWith("cancel-req-1");
      expect(screen.getByText("Owner Review: MCP Order Cancellation")).toBeTruthy();
      expect(screen.getByText("fp-cancel-456")).toBeTruthy();
      expect(screen.getByText("ACCEPTED")).toBeTruthy();
      expect(screen.getByText("2")).toBeTruthy();
      expect(screen.getByText("5")).toBeTruthy();
    });

    // Code is never issued on GET load
    expect(client.createCancellationMcpAuthorization).not.toHaveBeenCalled();
  });

  it("disables Issue code button until confirmation checkbox is checked for cancellation", async () => {
    vi.mocked(client.getCancellationRequest).mockResolvedValueOnce({
      cancellation_request: sampleCancellationRequest,
    });

    render(<OwnerReview action="cancel" requestId="cancel-req-1" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("fp-cancel-456")).toBeTruthy();
    });

    const issueButton = screen.getByRole("button", { name: "Issue code" });
    expect(issueButton.hasAttribute("disabled")).toBe(true);

    const checkbox = screen.getByRole("checkbox", {
      name: /I confirm this cancellation request/i,
    });
    fireEvent.click(checkbox);

    expect(issueButton.hasAttribute("disabled")).toBe(false);
  });

  it("issues cancellation authorization code only upon explicit confirmed action", async () => {
    vi.mocked(client.getCancellationRequest).mockResolvedValueOnce({
      cancellation_request: sampleCancellationRequest,
    });
    vi.mocked(client.createCancellationMcpAuthorization).mockResolvedValueOnce({
      authorization_id: "auth-cancel-123",
      code: "12345678",
      expires_at: "2026-09-25T20:05:00Z",
      cancellation_request: {
        ...sampleCancellationRequest,
        status: "authorized",
      },
    });

    render(<OwnerReview action="cancel" requestId="cancel-req-1" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("fp-cancel-456")).toBeTruthy();
      expect(screen.getByText("pending")).toBeTruthy();
    });

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I confirm this cancellation request/i,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Issue code" }));

    await waitFor(() => {
      expect(client.createCancellationMcpAuthorization).toHaveBeenCalledWith(
        "cancel-req-1",
        "fp-cancel-456",
        true,
      );
      expect(screen.getByText("12345678")).toBeTruthy();
      expect(screen.getByText("authorized")).toBeTruthy();
      expect(screen.getByText(/One-Time Authorization Code:/i)).toBeTruthy();
      expect(screen.getByRole("status")).toBeTruthy();
      expect(
        screen.getByText(/Provide this 8-digit code to the MCP client to authorize cancellation/i),
      ).toBeTruthy();
    });
  });

  it("displays alert and hides confirmation when cancellation request is not pending", async () => {
    vi.mocked(client.getCancellationRequest).mockResolvedValueOnce({
      cancellation_request: {
        ...sampleCancellationRequest,
        status: "invalidated",
        invalidation_reason: "order_state_changed",
      },
    });

    render(<OwnerReview action="cancel" requestId="cancel-req-1" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
      expect(
        screen.getByText(/This cancellation request is not pending \(status: invalidated, reason: order_state_changed\)/i),
      ).toBeTruthy();
    });

    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Issue code" })).toBeNull();
  });

  it("does not display an old cancellation code after the target changes", async () => {
    let resolveIssue!: (
      result: Awaited<
        ReturnType<typeof client.createCancellationMcpAuthorization>
      >,
    ) => void;
    vi.mocked(client.getCancellationRequest).mockImplementation(async (id) => ({
      cancellation_request: {
        ...sampleCancellationRequest,
        id,
        fingerprint: `${id}-fingerprint`,
      },
    }));
    vi.mocked(client.createCancellationMcpAuthorization).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveIssue = resolve;
        }),
    );

    const view = render(
      <OwnerReview
        action="cancel"
        requestId="cancel-one"
        onClose={vi.fn()}
      />,
    );
    await screen.findByText("cancel-one-fingerprint");
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I confirm this cancellation request/i,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Issue code" }));
    await waitFor(() => {
      expect(client.createCancellationMcpAuthorization).toHaveBeenCalledWith(
        "cancel-one",
        "cancel-one-fingerprint",
        true,
      );
    });

    view.rerender(
      <OwnerReview
        action="cancel"
        requestId="cancel-two"
        onClose={vi.fn()}
      />,
    );
    await screen.findByText("cancel-two-fingerprint");

    await act(async () => {
      resolveIssue({
        authorization_id: "auth-one",
        code: "87654321",
        expires_at: "2026-09-25T20:05:00Z",
        cancellation_request: {
          ...sampleCancellationRequest,
          id: "cancel-one",
          status: "authorized",
        },
      });
      await Promise.resolve();
    });

    expect(screen.queryByText("87654321")).toBeNull();
    expect(screen.getByText("cancel-two-fingerprint")).toBeTruthy();
  });

  it("closes the cancellation review and clears its code on hash navigation", async () => {
    vi.mocked(client.getCancellationRequest).mockResolvedValueOnce({
      cancellation_request: sampleCancellationRequest,
    });
    vi.mocked(client.createCancellationMcpAuthorization).mockResolvedValueOnce({
      authorization_id: "auth-nav",
      code: "12345678",
      expires_at: "2026-09-25T20:05:00Z",
      cancellation_request: {
        ...sampleCancellationRequest,
        status: "authorized",
      },
    });

    function ReviewHost() {
      const [open, setOpen] = useState(true);
      return open ? (
        <OwnerReview
          action="cancel"
          requestId={sampleCancellationRequest.id}
          onClose={() => setOpen(false)}
        />
      ) : (
        <p>Review closed</p>
      );
    }

    render(<ReviewHost />);
    await screen.findByText(sampleCancellationRequest.fingerprint);
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I confirm this cancellation request/i,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Issue code" }));
    await screen.findByText("12345678");

    act(() => window.dispatchEvent(new Event("hashchange")));

    expect(await screen.findByText("Review closed")).toBeTruthy();
    expect(screen.queryByText("12345678")).toBeNull();
  });
});
