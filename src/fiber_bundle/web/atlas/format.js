// Display formatting only. Unknown or missing values render as an explicit dash, never 0.
import { lang } from "./i18n.js";

const locale = () => (lang() === "zh" ? "zh-CN" : "en-US");
const ms = (value) => (value < 1e12 ? value * 1000 : value);
export const DASH = "—";

export function dateTime(value) {
  if (!Number.isFinite(value) || value <= 0) return DASH;
  return new Intl.DateTimeFormat(locale(), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(ms(value)));
}
export function shortDate(value) {
  if (!Number.isFinite(value) || value <= 0) return DASH;
  return new Intl.DateTimeFormat(locale(), {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(ms(value)));
}
export function timeOnly(value) {
  if (!Number.isFinite(value) || value <= 0) return DASH;
  return new Intl.DateTimeFormat(locale(), {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(ms(value)));
}
// "3 min ago" / "in 5 min"; falls back to the absolute date beyond a week.
export function relative(value, now = Date.now()) {
  if (!Number.isFinite(value) || value <= 0) return DASH;
  const diff = (ms(value) - now) / 1000,
    abs = Math.abs(diff);
  const format = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
  if (abs < 45) return format.format(0, "second");
  if (abs < 3600) return format.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return format.format(Math.round(diff / 3600), "hour");
  if (abs < 604800) return format.format(Math.round(diff / 86400), "day");
  return shortDate(value);
}
// Costs are integers in nano-USD.
export function usd(nusd) {
  if (!Number.isSafeInteger(nusd) || nusd < 0) return DASH;
  if (nusd > 0 && nusd < 1e7) return "< $0.01";
  return (
    "$" +
    (nusd / 1e9).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: nusd < 1e9 ? 4 : 2,
    })
  );
}
export function number(value) {
  if (!Number.isFinite(value)) return DASH;
  return value.toLocaleString(locale());
}
export function bytes(value) {
  if (!Number.isFinite(value) || value < 0) return DASH;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value,
    unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit++;
  }
  return `${unit ? size.toFixed(size < 10 ? 1 : 0) : size} ${units[unit]}`;
}
export function shortId(value) {
  return String(value || "").slice(0, 8);
}
export function firstLine(text, limit = 60) {
  const line = String(text || "")
    .split("\n")[0]
    .trim();
  return line.length > limit ? line.slice(0, limit) + "…" : line;
}
// Parse an "N USD" decimal into integer nano-USD; null when invalid.
export function parseUsd(text) {
  const value = String(text ?? "").trim();
  if (!/^\d+(\.\d{1,9})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  const result =
    Number(whole) * 1e9 + Number((fraction + "000000000").slice(0, 9));
  return Number.isSafeInteger(result) ? result : null;
}
