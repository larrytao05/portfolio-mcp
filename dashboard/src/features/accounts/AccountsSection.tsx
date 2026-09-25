import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  getAccount,
  type Account,
  type AccountCapability,
  type AccountDetail,
  type Position,
} from "../../api/client";
import { accountTypeLabel, displayValue, isStale } from "../../lib/portfolioDisplay";
import { capabilityLabel } from "../../lib/capabilityLabel";

type SortField =
  | "symbol"
  | "quantity"
  | "current_price"
  | "market_value"
  | "cost_basis"
  | "gain_loss";
type SortDirection = "ascending" | "descending";
const sortLabels: Record<SortField, string> = {
  symbol: "Holding",
  quantity: "Quantity",
  current_price: "Price",
  market_value: "Value",
  cost_basis: "Cost basis",
  gain_loss: "Gain/loss",
};
const sortFields = [
  "symbol",
  "quantity",
  "current_price",
  "market_value",
  "cost_basis",
  "gain_loss",
] as const satisfies readonly SortField[];
function compareDecimalValues(left: string | null, right: string | null) {
  if (left === right) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  const leftNegative = left.startsWith("-");
  const rightNegative = right.startsWith("-");
  if (leftNegative !== rightNegative) return leftNegative ? -1 : 1;
  const result = compareUnsignedDecimals(
    leftNegative ? left.slice(1) : left,
    rightNegative ? right.slice(1) : right,
  );
  return leftNegative ? -result : result;
}
function compareUnsignedDecimals(left: string, right: string) {
  const [leftWhole = "", leftFraction = ""] = left.split(".");
  const [rightWhole = "", rightFraction = ""] = right.split(".");
  const normalizedLeft = leftWhole.replace(/^0+/, "") || "0";
  const normalizedRight = rightWhole.replace(/^0+/, "") || "0";
  if (normalizedLeft.length !== normalizedRight.length)
    return normalizedLeft.length - normalizedRight.length;
  if (normalizedLeft !== normalizedRight)
    return normalizedLeft.localeCompare(normalizedRight);
  const precision = Math.max(leftFraction.length, rightFraction.length);
  return leftFraction
    .padEnd(precision, "0")
    .localeCompare(rightFraction.padEnd(precision, "0"));
}
function AccountSummary({ account }: { account: AccountDetail }) {
  return (
    <dl className="account-facts">
      <div>
        <dt>Provider</dt>
        <dd>{account.provider}</dd>
      </div>
      <div>
        <dt>Type</dt>
        <dd>{accountTypeLabel(account.account_type)}</dd>
      </div>
      <div>
        <dt>Currency</dt>
        <dd>{account.currency}</dd>
      </div>
      <div>
        <dt>Market value</dt>
        <dd className="numeric">
          {displayValue(
            account.balances.market_value,
            account.balances.currency,
          )}
        </dd>
      </div>
      <div>
        <dt>Cost basis</dt>
        <dd className="numeric">
          {displayValue(account.balances.cost_basis, account.balances.currency)}
        </dd>
      </div>
      <div>
        <dt>Saved at</dt>
        <dd>
          {account.is_stale || isStale(account.refreshed_at)
            ? "Stale saved data · "
            : "Saved data · "}
          {new Date(account.refreshed_at).toLocaleString()}
        </dd>
      </div>
      <div>
        <dt>Holdings as of</dt>
        <dd>{account.as_of ?? "Unavailable"}</dd>
      </div>
    </dl>
  );
}
function HoldingsTable({
  holdings,
  sort,
  updateSort,
}: {
  holdings: Position[];
  sort: { field: SortField; direction: SortDirection };
  updateSort: (field: SortField) => void;
}) {
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label="Scrollable holdings"
    >
      <table className="holdings-table">
        <thead>
          <tr>
            {sortFields.map((field) => (
              <th
                key={field}
                aria-sort={sort.field === field ? sort.direction : "none"}
                className={field === "symbol" ? "identity-column" : "numeric"}
                scope="col"
              >
                <button
                  aria-label={`Sort by ${sortLabels[field]}`}
                  onClick={() => updateSort(field)}
                  type="button"
                >
                  {sortLabels[field]}
                  {sort.field === field && (
                    <span aria-hidden="true">
                      {" "}
                      {sort.direction === "ascending" ? "↑" : "↓"}
                    </span>
                  )}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {holdings.map((position) => (
            <tr key={position.symbol}>
              <td className="identity-column">
                <strong>{position.symbol}</strong>
                <span>
                  {position.name} · {position.asset_class}
                </span>
              </td>
              <td className="numeric">{position.quantity}</td>
              <td className="numeric">
                {displayValue(position.current_price, position.currency)}
              </td>
              <td className="numeric">
                {displayValue(position.market_value, position.currency)}
              </td>
              <td className="numeric">
                {displayValue(position.cost_basis, position.currency)}
              </td>
              <td className="numeric">
                {displayValue(position.gain_loss, position.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function AccountHoldings({
  account,
  capability,
}: {
  account: AccountDetail;
  capability?: AccountCapability;
}) {
  const [filter, setFilter] = useState(""),
    [assetClass, setAssetClass] = useState("all"),
    [sort, setSort] = useState<{ field: SortField; direction: SortDirection }>({
      field: "symbol",
      direction: "ascending",
    });
  const assetClasses = useMemo(
    () => [...new Set(account.positions.map((p) => p.asset_class))].sort(),
    [account.positions],
  );
  const holdings = useMemo(() => {
    const search = filter.trim().toLowerCase();
    return [...account.positions]
      .filter((p) => assetClass === "all" || p.asset_class === assetClass)
      .filter(
        (p) =>
          !search ||
          [p.symbol, p.name, p.asset_class].some((v) =>
            v.toLowerCase().includes(search),
          ),
      )
      .sort((a, b) => {
        const c =
          sort.field === "symbol"
            ? a.symbol.localeCompare(b.symbol)
            : compareDecimalValues(a[sort.field], b[sort.field]);
        return sort.direction === "ascending" ? c : -c;
      });
  }, [account.positions, assetClass, filter, sort]);
  function updateSort(field: SortField) {
    setSort((current) => ({
      field,
      direction:
        current.field === field && current.direction === "ascending"
          ? "descending"
          : "ascending",
    }));
  }
  return (
    <details className="account-record" open>
      <summary>
        <span>
          <strong>{account.label}</strong>
          <small>
            {account.provider} · {accountTypeLabel(account.account_type)}
          </small>
        </span>
        <span className="summary-value">
          {displayValue(
            account.balances.market_value,
            account.balances.currency,
          )}{" "}
        </span>
      </summary>
      <div className="account-body">
        <AccountSummary account={account} />
        {capability && (
          <dl className="account-facts">
            <div>
              <dt>Execution capability</dt>
              <dd>{capabilityLabel(capability)}</dd>
            </div>
            <div>
              <dt>Supported orders</dt>
              <dd>{capability.order_types.join(", ") || "Unavailable"}</dd>
            </div>
            {capability.blocks.map((block) => (
              <div key={block.code}>
                <dt>Trading block</dt>
                <dd className="inline-alert" role="alert">
                  {block.message}
                  {block.recovery_action ? ` ${block.recovery_action}` : ""}
                </dd>
              </div>
            ))}
          </dl>
        )}
        <div className="section-heading">
          <div>
            <h3>Holdings</h3>
          </div>
          <span className="muted">{account.positions.length} records</span>
        </div>
        <div className="compact-controls">
          <label>
            Filter holdings
            <input
              aria-label="Filter holdings"
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Symbol, name, or asset class"
              value={filter}
            />
          </label>
          <label>
            Asset class
            <select
              onChange={(e) => setAssetClass(e.target.value)}
              value={assetClass}
            >
              <option value="all">All asset classes</option>
              {assetClasses.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        </div>
        {account.positions.length === 0 ? (
          <p className="state state-empty">
            This account has no saved holdings.
          </p>
        ) : holdings.length === 0 ? (
          <p className="state state-empty">
            No saved holdings match these filters.
          </p>
        ) : (
          <HoldingsTable
            holdings={holdings}
            sort={sort}
            updateSort={updateSort}
          />
        )}
      </div>
    </details>
  );
}

type AccountRecordProps = {
  account: Account;
  capability: AccountCapability | undefined;
};

function AccountRecord({ account, capability }: AccountRecordProps) {
  const detail = useQuery({
    queryKey: ["accounts", account.id],
    queryFn: () => getAccount(account.id),
  });

  return (
    <div>
      {!detail.data && (
        <div className="account-loading-identity">
          <strong>{account.label}</strong>
          <span>
            {account.provider} · {accountTypeLabel(account.account_type)}
          </span>
        </div>
      )}
      {(account.is_stale || isStale(account.source_refreshed_at)) && (
        <p className="stale-note" role="alert">
          <strong>Stale saved data</strong> · Last saved{" "}
          {new Date(account.source_refreshed_at).toLocaleString()}.
        </p>
      )}
      {detail.isPending && (
        <p className="state">Loading saved account details…</p>
      )}
      {detail.isError && (
        <p className="state state-error">
          Saved account details are unavailable. Refresh to try again.
        </p>
      )}
      {detail.data && (
        <AccountHoldings
          account={detail.data.account}
          capability={capability}
        />
      )}
    </div>
  );
}

type AccountsSectionProps = {
  accounts: readonly Account[] | undefined;
  capabilities: readonly AccountCapability[] | undefined;
  loading: boolean;
  error: boolean;
};

export function AccountsSection({
  accounts,
  capabilities,
  loading,
  error,
}: AccountsSectionProps) {
  return (
    <section
      className="content-section"
      id="accounts"
      aria-labelledby="accounts-heading"
    >
      <div className="section-heading">
        <div>
          <h2 id="accounts-heading">Accounts</h2>
        </div>
        <span className="muted">
          {accounts !== undefined ? `${accounts.length} saved accounts` : ""}
        </span>
      </div>
      {loading && <p className="state">Loading accounts…</p>}
      {error && (
        <p className="state state-error">
          Accounts are unavailable. Try refreshing the portfolio.
        </p>
      )}
      {accounts?.length === 0 && (
        <p className="state state-empty">
          Refresh your portfolio to load saved accounts.
        </p>
      )}
      {accounts && accounts.length > 0 && (
        <div className="account-list">
          {accounts.map((account) => (
            <AccountRecord
              key={account.id}
              account={account}
              capability={capabilities?.find(
                (item) => item.account_id === account.id,
              )}
            />
          ))}
        </div>
      )}
    </section>
  );
}
