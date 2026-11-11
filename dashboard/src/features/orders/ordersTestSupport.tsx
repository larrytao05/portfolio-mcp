import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

import { OrdersSection } from "./OrdersSection";
import type { Account, StoredOrder } from "../../api/client";

export function renderOrdersSection(accounts: Account[] = [sampleAccount]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <OrdersSection accounts={accounts} />
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

export const sampleAccount: Account = {
  id: "schwab-taxable-demo",
  provider: "schwab",
  label: "Schwab Taxable ••••4821",
  account_type: "taxable_brokerage",
  currency: "USD",
  is_stale: false,
  source_refreshed_at: "2026-09-12T20:00:00Z",
};

export const sampleOrder: StoredOrder = {
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
