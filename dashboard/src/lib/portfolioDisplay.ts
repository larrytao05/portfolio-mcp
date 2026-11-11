const quoteSourceLabels = new Map([
  ["alpaca_market_data", "Alpaca"],
  ["schwab_market_data", "Schwab market data"],
  ["fixture_market_data", "Fixture market data"],
]);

export function displayValue(
  value: string | null | undefined,
  currency?: string,
) {
  if (value === null || value === undefined) return "Unavailable";
  const [whole, fraction] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${grouped}${fraction === undefined ? "" : `.${fraction}`}${currency ? ` ${currency}` : ""}`;
}

export function quoteSourceLabel(source: string | null) {
  const normalizedSource = source?.trim();
  if (!normalizedSource) return "Unavailable";
  return quoteSourceLabels.get(normalizedSource) ?? "Unknown source";
}

export function accountTypeLabel(value: string) {
  if (value === "roth_ira") return "Roth IRA";
  if (value === "taxable_brokerage") return "Taxable brokerage";
  return value.replaceAll("_", " ");
}

export function isStale(date: string) {
  const timestamp = new Date(date).getTime();
  return !Number.isFinite(timestamp) || Date.now() - timestamp > 86400000;
}

export function compareDecimalValues(left: string | null, right: string | null) {
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
