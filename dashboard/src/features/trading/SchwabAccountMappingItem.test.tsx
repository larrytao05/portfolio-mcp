import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SchwabAccountMappingItem } from "./TradingPanels";
import type {
  SchwabAccountCandidate,
  SchwabAccountReadiness,
  StoredSchwabAccountMapping,
} from "../../api/client";

const api = vi.hoisted(() => ({
  getSchwabMapping: vi.fn(),
  getSchwabReadiness: vi.fn(),
  getSchwabMappingCandidates: vi.fn(),
  saveSchwabMapping: vi.fn(),
  deleteSchwabMapping: vi.fn(),
}));

vi.mock("../../api/client", () => api);

function renderItem(accountId: string = "schwab-taxable-demo") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SchwabAccountMappingItem accountId={accountId} />
    </QueryClientProvider>,
  );
}

const mockUnmappedReadiness: SchwabAccountReadiness = {
  account_id: "schwab-taxable-demo",
  state: "unmapped",
  ready: false,
  masked_account_number: null,
  message: "Account has not been mapped to a Schwab account hash",
  details: {},
};

const mockMappedReadiness: SchwabAccountReadiness = {
  account_id: "schwab-taxable-demo",
  state: "ready",
  ready: true,
  masked_account_number: "*1234",
  message: "Schwab execution is ready for this account",
  details: { account_type: "MARGIN" },
};

const mockExistingMapping: StoredSchwabAccountMapping = {
  id: "map-demo-1",
  account_id: "schwab-taxable-demo",
  masked_account_number: "*1234",
  created_at: "2026-09-20T12:00:00Z",
  updated_at: "2026-09-20T12:00:00Z",
};

const mockCandidates: SchwabAccountCandidate[] = [
  {
    candidate_id: "cand-hmac-1",
    masked_account_number: "*1234",
    is_mapped: false,
    mapped_to_account_id: null,
    suggested: true,
  },
  {
    candidate_id: "cand-hmac-2",
    masked_account_number: "*5678",
    is_mapped: true,
    mapped_to_account_id: "other-account",
    suggested: false,
  },
];

describe("SchwabAccountMappingItem", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders unmapped state and allows mapping via candidate selector and confirmation", async () => {
    api.getSchwabMapping.mockRejectedValue(new Error("Not found"));
    api.getSchwabReadiness.mockResolvedValue({ readiness: mockUnmappedReadiness });
    api.getSchwabMappingCandidates.mockResolvedValue({
      account_id: "schwab-taxable-demo",
      candidates: mockCandidates,
    });
    api.saveSchwabMapping.mockResolvedValue({
      mapping: mockExistingMapping,
    });

    renderItem("schwab-taxable-demo");

    expect(
      await screen.findByRole("heading", {
        name: "Schwab Execution Readiness & Mapping",
      }),
    ).toBeTruthy();
    const statusEl = await screen.findByText(/Status:/i);
    expect(statusEl.textContent).toContain("UNMAPPED");

    const mapButton = screen.getByRole("button", { name: "Map to Schwab account" });
    fireEvent.click(mapButton);

    const candidateHeading = await screen.findByRole("heading", {
      name: "Select Schwab Candidate Account",
    });
    expect(candidateHeading).toBeTruthy();
    expect(api.getSchwabMappingCandidates).toHaveBeenCalledWith("schwab-taxable-demo");

    expect(await screen.findByText("*1234")).toBeTruthy();
    expect(screen.getByText("[Suggested match]")).toBeTruthy();
    expect(screen.getByText("*5678")).toBeTruthy();
    expect(screen.getByText("(Already mapped)")).toBeTruthy();

    const saveButton = screen.getByRole("button", {
      name: "Confirm and save mapping",
    }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);

    // Select candidate
    const radio1 = screen.getByDisplayValue("cand-hmac-1");
    fireEvent.click(radio1);
    expect(saveButton.disabled).toBe(true);

    // Check confirmation checkbox
    const confirmCheckbox = screen.getByLabelText(
      "I confirm this is the correct Schwab trading account",
    );
    fireEvent.click(confirmCheckbox);
    expect(saveButton.disabled).toBe(false);

    // Save
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.saveSchwabMapping).toHaveBeenCalledWith("schwab-taxable-demo", {
        candidate_id: "cand-hmac-1",
        confirmed: true,
      });
    });
  });

  it("renders mapped state with masked account and supports revoking mapping", async () => {
    api.getSchwabMapping.mockResolvedValue({ mapping: mockExistingMapping });
    api.getSchwabReadiness.mockResolvedValue({ readiness: mockMappedReadiness });
    api.deleteSchwabMapping.mockResolvedValue({
      deleted: true,
      account_id: "schwab-taxable-demo",
    });

    renderItem("schwab-taxable-demo");

    const mappedText = await screen.findByText(/Mapped to Schwab account:/i);
    expect(mappedText.textContent).toContain("*1234");
    expect(screen.getByText(/Status:/i).textContent).toContain("READY");

    const revokeButton = screen.getByRole("button", { name: "Revoke Schwab mapping" });
    fireEvent.click(revokeButton);

    await waitFor(() => {
      expect(api.deleteSchwabMapping).toHaveBeenCalledWith("schwab-taxable-demo");
    });
  });

  it("displays error message when saving mapping fails", async () => {
    api.getSchwabMapping.mockRejectedValue(new Error("Not found"));
    api.getSchwabReadiness.mockResolvedValue({ readiness: mockUnmappedReadiness });
    api.getSchwabMappingCandidates.mockResolvedValue({
      account_id: "schwab-taxable-demo",
      candidates: mockCandidates,
    });
    api.saveSchwabMapping.mockRejectedValue(new Error("Account already mapped to another entity"));

    renderItem("schwab-taxable-demo");

    const mapButton = await screen.findByRole("button", { name: "Map to Schwab account" });
    fireEvent.click(mapButton);

    const radio1 = await screen.findByDisplayValue("cand-hmac-1");
    fireEvent.click(radio1);

    const confirmCheckbox = screen.getByLabelText(
      "I confirm this is the correct Schwab trading account",
    );
    fireEvent.click(confirmCheckbox);

    const saveButton = screen.getByRole("button", { name: "Confirm and save mapping" });
    fireEvent.click(saveButton);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Account already mapped to another entity");
  });
});
