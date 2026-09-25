import type { AccountCapability } from "../api/client";

export function capabilityLabel(capability: AccountCapability) {
  if (capability.is_trade_capable) return "Trade-capable";
  if (capability.blocks.some((block) => block.code === "monitoring_only"))
    return "Monitoring only";
  if (capability.is_stale) return "Stale — trading blocked";
  return "Trading blocked";
}
