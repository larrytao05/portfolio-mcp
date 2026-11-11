import { useEffect, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  getAccounts,
  getHealth,
  getLatestRefresh,
  getTradingStatus,
  getOverview,
  getOverviewHistory,
  refreshPortfolio,
} from "./api/client";
import { ActivitySection } from "./features/activity/ActivitySection";
import { ConnectionsSection } from "./features/connections/ConnectionsSection";
import { AccountsSection } from "./features/accounts/AccountsSection";
import { MarketDataSection } from "./features/market-data/MarketDataSection";
import { PositionsSection } from "./features/positions/PositionsSection";
import { OverviewSection } from "./features/overview/OverviewSection";
import { RecordStrip } from "./features/record/RecordStrip";
import { ExecutionStatus, TradeSection, TradingSettingsPanel } from "./features/trading/TradingPanels";

function revealSection(id: string) {
  const target = document.getElementById(id);
  let parent = target?.parentElement;
  while (parent) {
    if (parent instanceof HTMLDetailsElement) parent.open = true;
    parent = parent.parentElement;
  }
  target?.scrollIntoView?.({ block: "start" });
}

function Navigation() {
  const [hash, setHash] = useState(window.location.hash || "#overview");
  useEffect(() => {
    const update = () => {
      setHash(window.location.hash || "#overview");
      revealSection(window.location.hash.slice(1));
    };
    update();
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return (
    <nav aria-label="Primary navigation">
      {[
        ["overview", "Overview"],
        ["positions", "Positions"],
        ["accounts", "Accounts"],
        ["connections", "Connections"],
        ["activity", "Activity"],
        ["market-data", "Market data"],
        ["settings", "Settings"],
        ["trade", "Trade"],
      ].map(([id, label]) => (
        <a
          key={id}
          href={`#${id}`}
          onClick={() => revealSection(id)}
          aria-current={hash === `#${id}` ? "location" : undefined}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: getAccounts });
  const latestRefresh = useQuery({
    queryKey: ["refreshes", "latest"],
    queryFn: getLatestRefresh,
  });
  const tradingStatus = useQuery({ queryKey: ["trading", "status"], queryFn: getTradingStatus });
  const overviewQuery = useQuery({
    queryKey: ["overview"],
    queryFn: getOverview,
  });
  const overviewHistoryQuery = useQuery({
    queryKey: ["overview", "history"],
    queryFn: getOverviewHistory,
  });
  const refresh = useMutation({
    mutationFn: refreshPortfolio,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["connections"] }),
        queryClient.invalidateQueries({ queryKey: ["refreshes"] }),
        queryClient.invalidateQueries({ queryKey: ["activity"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
        queryClient.invalidateQueries({ queryKey: ["trading", "status"] }),
      ]);
    },
  });
  const shownRefresh = refresh.data?.refresh ?? latestRefresh.data?.refresh;
  return (
    <div className="app-shell">
      <aside className="side-rail">
        <a className="wordmark" href="#top">
          <span className="wordmark-mark">P</span>
          <span>
            Portfolio
            <br />
            <em>record</em>
          </span>
        </a>
        <Navigation />
        <div className="rail-footer">
          <span
            className={`connection-dot ${health.isError ? "connection-error" : health.isPending ? "connection-pending" : ""}`}
            aria-hidden="true"
          />{" "}
          API{" "}
          {health.isPending
            ? "checking"
            : health.isError
              ? "unavailable"
              : (health.data?.status ?? "unavailable")}
        </div>
      </aside>
      <main id="top" className="main-content">
        <header className="page-header">
          <div>
            <p className="kicker">Your investments, together</p>
            <h1>Portfolio Dashboard</h1>
            <p className="lede">
              A clear view across all your accounts.
            </p>
          </div>
          <button
            className="refresh-button"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
            type="button"
          >
            <span aria-hidden="true">↻</span>
            {refresh.isPending ? "Refreshing…" : "Refresh portfolio"}
          </button>
        </header>
        <RecordStrip
          refresh={shownRefresh}
          accounts={accounts.data?.accounts}
          loading={latestRefresh.isPending || accounts.isPending}
          unavailable={latestRefresh.isError || accounts.isError}
        />
        {shownRefresh?.warnings.map((w) => (
          <p
            className={
              shownRefresh.status === "failed" ? "inline-alert" : "stale-note"
            }
            key={w}
            role="alert"
          >
            {w}
          </p>
        ))}
        {refresh.isError && (
          <p className="inline-alert" role="alert">
            Refresh failed. Your saved data is unchanged.
          </p>
        )}
        <OverviewSection
          overview={overviewQuery.data?.overview}
          history={overviewHistoryQuery.data?.history ?? overviewQuery.data?.overview.history}
          loading={overviewQuery.isPending}
          error={overviewQuery.isError}
          historyLoading={overviewHistoryQuery.isPending}
          historyError={overviewHistoryQuery.isError}
        />
        <PositionsSection accounts={accounts.data?.accounts} loading={accounts.isPending} error={accounts.isError} />
        <details className="workspace-panel">
          <summary>Connections</summary>
          <ConnectionsSection />
        </details>
        <details className="workspace-panel">
          <summary>Account details</summary>
          <AccountsSection
            accounts={accounts.data?.accounts}
            capabilities={tradingStatus.data?.accounts}
            loading={accounts.isPending}
            error={accounts.isError}
          />
        </details>
        <details className="workspace-panel">
          <summary>Activity</summary>
          <ActivitySection accounts={accounts.data?.accounts ?? []} />
        </details>
        <details className="workspace-panel">
          <summary>Market data</summary>
          <MarketDataSection accounts={accounts.data?.accounts ?? []} />
        </details>
        <details className="workspace-panel">
          <summary>Trading &amp; settings</summary>
          <ExecutionStatus status={tradingStatus.data} loading={tradingStatus.isPending} unavailable={tradingStatus.isError} />
          <TradingSettingsPanel />
          <TradeSection accounts={accounts.data?.accounts ?? []} />
        </details>
      </main>
    </div>
  );
}
