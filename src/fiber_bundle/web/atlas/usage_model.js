// Ledger presentation: missing prices and incomplete reads are never a known zero.
import { lang } from "./i18n.js";
import { usd } from "./format.js";
export function knownCost(row) {
  if (
    row?.available === false ||
    !Number.isSafeInteger(row?.estimated_nusd) ||
    row.estimated_nusd < 0 ||
    (row.calls > 0 && row.settled_calls === 0)
  )
    return null;
  return row.estimated_nusd;
}
export function ledgerCost(row) {
  if (row?.available === false) return "—";
  if (row?.calls > 0 && row.settled_calls === 0)
    return lang() === "en" ? "Unknown" : "未知";
  return (
    usd(knownCost(row)) +
    (row?.unsettled_calls > 0
      ? lang() === "en"
        ? " (partly unknown)"
        : "（部分未知）"
      : "")
  );
}
export function summaryCost(cost) {
  return ledgerCost({
    available: cost.complete,
    estimated_nusd: cost.total,
    calls: cost.calls,
    settled_calls: cost.allUnknown ? 0 : cost.calls,
    unsettled_calls: cost.unsettled,
  });
}
