// Settings and usage contracts stay separate from rendering and the shared HTTP boundary.
const metrics = [
  "calls",
  "settled_calls",
  "unsettled_calls",
  "estimated_nusd",
  "reserved_nusd",
  "input_tokens",
  "output_tokens",
];
const breakdown = [
  "price_unknown_calls",
  "pending_calls",
  "result_unknown_calls",
];
const integer = (value) => Number.isSafeInteger(value) && value >= 0;
const day = (value) =>
  typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
const keyId = (value) =>
  value === "unattributed" ||
  (typeof value === "string" && /^key_[0-9a-f]{64}$/.test(value));
function amounts(value) {
  return (
    value &&
    (breakdown.every((name) => value[name] === undefined) ||
      (breakdown.every((name) => integer(value[name])) &&
        breakdown.reduce((sum, name) => sum + value[name], 0) ===
          value.unsettled_calls)) &&
    metrics.every((name) => integer(value[name])) &&
    (value.token_unknown_calls === undefined ||
      (integer(value.token_unknown_calls) &&
        value.token_unknown_calls <= value.calls)) &&
    value.calls === value.settled_calls + value.unsettled_calls &&
    integer(value.input_tokens + value.output_tokens)
  );
}
function key(value) {
  return (
    value &&
    keyId(value.id) &&
    typeof value.label === "string" &&
    typeof value.configured === "boolean" &&
    Array.isArray(value.models) &&
    value.models.every((model) => typeof model === "string")
  );
}
export function validUsageReport(value) {
  if (
    !value ||
    !day(value.start) ||
    !day(value.end) ||
    value.timezone !== "UTC" ||
    value.currency !== "USD" ||
    value.cost_basis !== "configured_price_estimate" ||
    value.count_basis !== "recorded_reservations" ||
    value.credential_basis !== "credential_file_reference" ||
    !amounts(value.summary) ||
    (value.lifetime !== undefined && !amounts(value.lifetime)) ||
    !Array.isArray(value.daily) ||
    value.daily.length < 1 ||
    value.daily.length > 366 ||
    !value.daily.every((item) => day(item.day) && amounts(item)) ||
    !Array.isArray(value.keys) ||
    !value.keys.every(key) ||
    !Array.isArray(value.by_key) ||
    !value.by_key.every((item) => key(item) && amounts(item))
  )
    return false;
  const fields =
    value.summary.price_unknown_calls === undefined
      ? [...metrics]
      : [...metrics, ...breakdown];
  if (value.summary.token_unknown_calls !== undefined)
    fields.push("token_unknown_calls");
  return fields.every((name) => {
    const daily = value.daily.reduce((sum, item) => sum + item[name], 0);
    const keys = value.by_key.reduce((sum, item) => sum + item[name], 0);
    return (
      integer(daily) &&
      integer(keys) &&
      daily === value.summary[name] &&
      keys === daily
    );
  });
}
export function validSystemSettings(value) {
  return (
    value &&
    Number.isInteger(value.session_ttl) &&
    value.session_ttl >= 1 &&
    value.session_ttl <= 86400 &&
    integer(value.revision) &&
    value.timing === "fixed_from_login" &&
    value.effective === "next_login"
  );
}
export function createSystemApi(request) {
  return {
    modelBudgets: () => request("/admin/system/model-budgets"),
    saveModelBudget: (body) =>
      request("/admin/system/model-budgets", "PUT", body),
    systemFonts: () =>
      request("/admin/system/fonts", "GET", undefined, validSystemFonts),
    systemSettings: () =>
      request("/admin/system/settings", "GET", undefined, validSystemSettings),
    saveSystemSettings: (body) =>
      request("/admin/system/settings", "PUT", body, validSystemSettings),
    usageReport: (filters) =>
      request(
        "/admin/system/usage?" + new URLSearchParams(filters),
        "GET",
        undefined,
        validUsageReport,
      ),
  };
}

export function validSystemFonts(value) {
  const name = (text) =>
    typeof text === "string" &&
    text.length > 0 &&
    text.length <= 256 &&
    !Array.from(text).some(
      (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127,
    );
  return Boolean(
    value &&
    typeof value.supported === "boolean" &&
    Array.isArray(value.fonts) &&
    value.fonts.length <= 16384 &&
    (value.supported || value.fonts.length === 0) &&
    value.fonts.every(
      (font) =>
        font &&
        Object.keys(font).length === 3 &&
        ["family", "postscriptName", "style"].every((key) => name(font[key])),
    ),
  );
}
