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

  it("does not show an issued code after the reviewed target changes", async () => {
    let resolveIssue!: (
      result: Awaited<ReturnType<typeof client.issueMcpAuthorization>>,
    ) => void;
    vi.mocked(client.getOrderDraft).mockImplementation(async (draftId) => ({
      draft: {
        ...sampleDraft,
        id: draftId,
        fingerprint: `${draftId}-fingerprint`,
      },
    }));
    vi.mocked(client.issueMcpAuthorization).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveIssue = resolve;
        }),
    );

    const view = render(
      <OwnerReview action="submit" draftId="draft-one" onClose={vi.fn()} />,
    );
    await screen.findByText("draft-one-fingerprint");
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I confirm this order instruction/i,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Issue code" }));
    await waitFor(() => {
      expect(client.issueMcpAuthorization).toHaveBeenCalledWith(
        "draft-one",
        "draft-one-fingerprint",
        true,
      );
    });

    view.rerender(
      <OwnerReview action="submit" draftId="draft-two" onClose={vi.fn()} />,
    );
    await screen.findByText("draft-two-fingerprint");

    await act(async () => {
      resolveIssue({
        authorization_id: "auth-one",
        code: "87654321",
        expires_at: "2026-09-25T20:05:00Z",
        draft: {
          id: "draft-one",
          symbol: "VTI",
          side: "buy",
          quantity: "5",
          order_type: "limit",
          limit_price: "220.00",
          fingerprint: "draft-one-fingerprint",
        },
      });
      await Promise.resolve();
    });

    expect(screen.queryByText("87654321")).toBeNull();
    expect(screen.getByText("draft-two-fingerprint")).toBeTruthy();
  });

  it("closes and clears a displayed code after hash navigation", async () => {
    vi.mocked(client.getOrderDraft).mockResolvedValueOnce({ draft: sampleDraft });
    vi.mocked(client.issueMcpAuthorization).mockResolvedValueOnce({
      authorization_id: "auth-nav",
      code: "12345678",
      expires_at: "2026-09-25T20:05:00Z",
      draft: {
        id: sampleDraft.id,
        symbol: "VTI",
        side: "buy",
        quantity: "5",
        order_type: "limit",
        limit_price: "220.00",
        fingerprint: sampleDraft.fingerprint,
      },
    });

    function ReviewHost() {
      const [open, setOpen] = useState(true);
      return open ? (
        <OwnerReview
          action="submit"
          draftId={sampleDraft.id}
          onClose={() => setOpen(false)}
        />
      ) : (
        <p>Review closed</p>
      );
    }

    render(<ReviewHost />);
    await screen.findByText(sampleDraft.fingerprint);
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I confirm this order instruction/i,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Issue code" }));
    await screen.findByText("12345678");

    act(() => window.dispatchEvent(new Event("hashchange")));

    expect(await screen.findByText("Review closed")).toBeTruthy();
    expect(screen.queryByText("12345678")).toBeNull();
  });
});
