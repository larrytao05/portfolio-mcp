import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const api = vi.hoisted(() => ({
  getAccounts: vi.fn(),
  getHealth: vi.fn(),
  refreshPortfolio: vi.fn(),
}));

vi.mock("./api/client", () => api);

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App", () => {
  beforeEach(() => {
    api.getHealth.mockResolvedValue({ status: "ok" });
    api.getAccounts.mockResolvedValue({ accounts: [] });
    api.refreshPortfolio.mockResolvedValue({
      refresh: {
        id: 1,
        status: "success",
        started_at: "2026-09-12T14:00:00+00:00",
        completed_at: "2026-09-12T14:00:01+00:00",
        accounts_refreshed: 2,
        positions_refreshed: 9,
        daily_snapshots_recorded: 2,
      },
    });
  });

  it("refreshes persisted portfolio data from the dashboard", async () => {
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "Refresh portfolio" }));

    await waitFor(() => expect(api.refreshPortfolio).toHaveBeenCalledOnce());
    expect(await screen.findByText(/Saved 2 accounts and 9 positions/)).toBeTruthy();
  });
});
