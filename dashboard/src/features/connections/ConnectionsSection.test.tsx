import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { ConnectionsSection } from "./ConnectionsSection";

const api = vi.hoisted(() => ({
  getConnections: vi.fn(), connectProvider: vi.fn(), disconnectProvider: vi.fn(),
  getSnapTradePortal: vi.fn(), reactivateConnection: vi.fn(), updateAccountConnection: vi.fn(),
}));
vi.mock("../../api/client", () => api);
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("requires an explicit verification action to restore a disconnected SnapTrade connection", async () => {
  api.getConnections.mockResolvedValue({ configured: { snaptrade: true }, accounts: [{ account_id: "fictional", label: "Fixture account", connection_id: "snaptrade:auth", provider_route: "snaptrade", active: false, included: true, trade_enabled: false, version: 1 }] });
  api.reactivateConnection.mockResolvedValue({ status: "connected" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ConnectionsSection /></QueryClientProvider>);
  expect(await screen.findByText("Fixture account")).toBeTruthy();
  expect(api.reactivateConnection).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Verify and restore connection" }));
  await waitFor(() => expect(api.reactivateConnection.mock.calls[0]?.[0]).toBe("snaptrade:auth"));
});

it("shows affected accounts and requires confirmation before disconnect", async () => {
  api.getConnections.mockResolvedValue({ configured: { fixture: true }, accounts: [
    { account_id: "one", label: "First account", connection_id: "fixture", provider_route: "fixture", active: true, included: true, trade_enabled: false, version: 0 },
    { account_id: "two", label: "Second account", connection_id: "fixture", provider_route: "fixture", active: true, included: true, trade_enabled: false, version: 0 },
  ] });
  api.disconnectProvider.mockResolvedValue({});
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ConnectionsSection /></QueryClientProvider>);
  const buttons = await screen.findAllByRole("button", { name: "Disconnect provider" });
  fireEvent.click(buttons[0]);
  expect(screen.getByRole("group", { name: "Confirm disconnect" }).textContent).toContain("First account");
  expect(screen.getByRole("group", { name: "Confirm disconnect" }).textContent).toContain("Second account");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.disconnectProvider).not.toHaveBeenCalled();
  fireEvent.click(buttons[0]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm disconnect" }));
  await waitFor(() => expect(api.disconnectProvider.mock.calls[0]?.[0]).toBe("fixture"));
});
