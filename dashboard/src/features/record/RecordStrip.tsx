import { type Account, type RefreshResult } from "../../api/client";
import { StatusPill } from "../../components/StatusPill";
import { isStale } from "../../lib/portfolioDisplay";

export function RecordStrip({
  refresh,
  accounts,
  loading,
  unavailable,
}: {
  refresh: RefreshResult | null | undefined;
  accounts: Account[] | undefined;
  loading: boolean;
  unavailable: boolean;
}) {
  if (loading && !refresh)
    return (
      <div className="record-strip record-strip-empty" role="status">
        Loading saved record…
      </div>
    );
  if (unavailable)
    return (
      <div className="record-strip record-failed" role="alert">
        Saved record status is unavailable. Refresh to check again; any visible
        values are previously saved data.
      </div>
    );
  if (!refresh)
    return (
      <div className="record-strip record-strip-empty">
        <span className="record-mark">◌</span>
        <div>
          <strong>No saved record yet</strong>
          <span>Refresh to import provider data into this workspace.</span>
        </div>
      </div>
    );
  const staleAccounts = (accounts ?? []).filter(
    (account) => account.is_stale || isStale(account.source_refreshed_at),
  );
  const label =
    refresh.status === "success"
      ? staleAccounts.length || isStale(refresh.completed_at)
        ? "stale"
        : "fresh"
      : refresh.status;
  const coverage = refresh.provider_outcomes
    .map(
      (outcome) =>
        `${outcome.provider}: ${outcome.accounts_refreshed} refreshed, ${outcome.stale_accounts} stale, ${outcome.excluded_accounts} excluded`,
    )
    .join(" · ");
  return (
    <div className={`record-strip record-${label}`} role="status">
      <span className="record-mark" aria-hidden="true">
        {label === "fresh" ? "✓" : "!"}
      </span>
      <div>
        <strong>
          {label === "fresh"
            ? "Saved record is current"
            : label === "stale"
              ? "Saved record is stale"
              : `Refresh ${label}`}
        </strong>
        <span>
          Last refresh: {new Date(refresh.completed_at).toLocaleString()}
        </span>
        <span>{`Saved ${refresh.accounts_refreshed} accounts and ${refresh.positions_refreshed} positions`}</span>
        {coverage && <span>Coverage: {coverage}</span>}
        {staleAccounts.map((account) => (
          <span key={account.id}>
            {account.label}: stale saved data from{" "}
            {new Date(account.source_refreshed_at).toLocaleString()}
          </span>
        ))}
        {refresh.status === "failed" && (
          <span>Refresh failed. Your saved data is unchanged.</span>
        )}
      </div>
      <StatusPill status={label} />
    </div>
  );
}
