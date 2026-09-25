export function displayValue(
  value: string | null | undefined,
  currency?: string,
) {
  if (value === null || value === undefined) return "Unavailable";
  const [whole, fraction] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${grouped}${fraction === undefined ? "" : `.${fraction}`}${currency ? ` ${currency}` : ""}`;
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
