import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { type Account, type ActivityFilters, getActivity } from "../../api/client";
import { displayValue } from "../../lib/portfolioDisplay";

export function ActivitySection({ accounts }: { accounts: Account[] }) {
  const [filters, setFilters] = useState<ActivityFilters>({}),
    [offset, setOffset] = useState(0);
  const activity = useQuery({
    queryKey: ["activity", filters, offset],
    queryFn: () => getActivity({ ...filters, limit: 50, offset }),
  });
  const pagination = activity.data?.pagination;
  const next =
    pagination !== undefined && offset + pagination.limit < pagination.total;
  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const text = (name: string) => {
      const value = data.get(name);
      return typeof value === "string" && value !== "" ? value : undefined;
    };
    setFilters({
      account_id: text("account_id"),
      provider: text("provider"),
      type: text("type"),
      symbol: text("symbol"),
      start_date: text("start_date"),
      end_date: text("end_date"),
    });
    setOffset(0);
  }
  return (
    <section
      className="content-section"
      id="activity"
      aria-labelledby="activity-heading"
    >
      <div className="section-heading">
        <div>
          <h2 id="activity-heading">Activity</h2>
        </div>
        <span className="muted">Latest saved events</span>
      </div>
      <form className="filter-bar" onSubmit={submitFilters}>
        <label>
          Account
          <select
            aria-label="Activity account"
            defaultValue=""
            name="account_id"
          >
            <option value="">All accounts</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Provider
          <input
            aria-label="Activity provider"
            defaultValue=""
            name="provider"
          />
        </label>
        <label>
          Type
          <input aria-label="Activity type" defaultValue="" name="type" />
        </label>
        <label>
          Symbol
          <input aria-label="Activity symbol" defaultValue="" name="symbol" />
        </label>
        <label>
          From
          <input
            aria-label="Activity start date"
            defaultValue=""
            name="start_date"
            type="date"
          />
        </label>
        <label>
          To
          <input
            aria-label="Activity end date"
            defaultValue=""
            name="end_date"
            type="date"
          />
        </label>
        <button type="submit">Apply activity filters</button>
      </form>
      {activity.isPending && <p className="state">Loading activity…</p>}
      {activity.isError && (
        <p className="state state-error">
          Activity is unavailable. Try again after the next refresh.
        </p>
      )}
      {activity.data?.activities.length === 0 && (
        <p className="state state-empty">No activity matches these filters.</p>
      )}
      {activity.data && activity.data.activities.length > 0 && (
        <div className="activity-list">
          {activity.data.activities.map((item) => (
            <article className="activity-row" key={item.id}>
              <div className="activity-date">
                {item.occurred_at
                  ? new Date(item.occurred_at).toLocaleString()
                  : item.occurred_on}
              </div>
              <div>
                <strong>
                  {item.type}
                  {item.symbol ? ` · ${item.symbol}` : ""}
                </strong>
                <span>
                  {item.provider} · {item.description} · {item.account.label}
                </span>
              </div>
              <div className="numeric activity-amount">
                {displayValue(item.amount, item.currency)}
                <small>
                  Imported {new Date(item.imported_at).toLocaleString()}
                </small>
              </div>
            </article>
          ))}
        </div>
      )}
      {pagination && pagination.total > 0 && (
        <nav className="pagination" aria-label="Activity pagination">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(offset - pagination.limit)}
            type="button"
          >
            Previous activity page
          </button>
          <span>
            Showing {offset + 1}-
            {Math.min(offset + pagination.limit, pagination.total)} of{" "}
            {pagination.total}
          </span>
          <button
            disabled={!next}
            onClick={() => setOffset(offset + pagination.limit)}
            type="button"
          >
            Next activity page
          </button>
        </nav>
      )}
    </section>
  );
}
