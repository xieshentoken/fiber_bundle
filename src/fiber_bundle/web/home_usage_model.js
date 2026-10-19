// Thirty UTC days and the thirty before them, from one bounded 60-day usage read.
export function costSummary(report, today = new Date()) {
  if (!report) return null;
  const end = Date.UTC(
    today.getUTCFullYear(),
    today.getUTCMonth(),
    today.getUTCDate(),
  );
  const byDay = new Map(report.daily.map((d) => [d.day, d]));
  const pick = (from, to) =>
    Array.from({ length: from - to + 1 }, (_, i) => {
      const day = new Date(end - (from - i) * 86400000)
        .toISOString()
        .slice(0, 10);
      const found = byDay.get(day);
      return found
        ? { ...found, available: true }
        : {
            day,
            available: false,
            estimated_nusd: 0,
            reserved_nusd: 0,
            calls: 0,
            settled_calls: 0,
            unsettled_calls: 0,
          };
    });
  const current = pick(29, 0),
    previous = pick(59, 30);
  const sum = (rows, key) =>
    rows.reduce((total, row) => total + (row[key] || 0), 0);
  const complete = current.every((d) => d.available),
    comparisonComplete = previous.every((d) => d.available);
  const total = sum(current, "estimated_nusd"),
    before = sum(previous, "estimated_nusd");
  const unsettled = sum(current, "unsettled_calls"),
    settled = sum(current, "settled_calls"),
    calls = sum(current, "calls");
  const month = new Date(end).toISOString().slice(0, 7);
  return {
    days: current,
    lifetime: report.lifetime || null,
    inputTokens: complete ? sum(current, "input_tokens") : null,
    outputTokens: complete ? sum(current, "output_tokens") : null,
    tokenUnknownCalls: sum(current, "token_unknown_calls"),
    total: complete ? total : null,
    previous: comparisonComplete ? before : null,
    complete,
    unsettled,
    allUnknown: calls > 0 && settled === 0,
    change:
      complete &&
      comparisonComplete &&
      !unsettled &&
      !sum(previous, "unsettled_calls") &&
      before > 0
        ? (total - before) / before
        : null,
    monthUnknown: sum(
      current.filter((d) => d.day.startsWith(month)),
      "unsettled_calls",
    ),
    monthAllUnknown:
      sum(
        current.filter((d) => d.day.startsWith(month)),
        "calls",
      ) > 0 &&
      sum(
        current.filter((d) => d.day.startsWith(month)),
        "settled_calls",
      ) === 0,
    monthToDate: complete
      ? sum(
          current.filter((d) => d.day.startsWith(month)),
          "estimated_nusd",
        )
      : null,
    reserved: complete ? sum(current, "reserved_nusd") : null,
    calls: complete ? calls : null,
    start: current[0].day,
    end: current.at(-1).day,
    localCalls: Number.isSafeInteger(report.local_calls)
      ? report.local_calls
      : null,
  };
}
export function costWindow(today = new Date()) {
  const end = new Date(
    Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()),
  );
  const start = new Date(end.getTime() - 59 * 86400000);
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}
