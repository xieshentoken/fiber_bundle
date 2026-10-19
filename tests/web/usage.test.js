import test from "node:test";
import assert from "node:assert/strict";
import {
  money,
  chartGeometry,
} from "../../src/fiber_bundle/web/usage_chart.js";
import { periodDates } from "../../src/fiber_bundle/web/usage.js";
import {
  createSystemApi,
  validUsageReport,
  validSystemSettings,
} from "../../src/fiber_bundle/web/system_api.js";

test("USD display keeps tiny nonzero costs and excludes invalid amounts", () => {
  assert.equal(money(0), "$0.00");
  assert.equal(money(1), "$0.000000001");
  assert.equal(money(25000), "$0.000025");
  assert.equal(money(1000000000), "$1.00");
  assert.equal(money(-1), "—");
  assert.equal(money(Number.MAX_SAFE_INTEGER + 1), "—");
});
test("chart uses separated count/cost panels and never treats reservations as cost", () => {
  const chart = chartGeometry([
    {
      day: "2026-10-01",
      calls: 4,
      estimated_nusd: 100,
      reserved_nusd: 1000000,
    },
    { day: "2026-10-02", calls: 0, estimated_nusd: 0, reserved_nusd: 0 },
  ]);
  assert(
    chart.callTop > chart.top + chart.plotHeight,
    "different measures occupy separate panels",
  );
  assert.equal(chart.callMax, 4);
  assert.equal(chart.costMax, 100);
  assert.equal(chart.points[0].callY, chart.callTop);
  assert.equal(chart.points[0].costY, chart.top);
  assert.equal(chart.points[1].callY, chart.callTop + chart.plotHeight);
  assert.equal(chart.points[1].costY, chart.top + chart.plotHeight);
});
test("date presets use UTC and include the final day across leap/month boundaries", () => {
  assert.deepEqual(periodDates("7", new Date("2024-03-01T23:59:59Z")), {
    start: "2024-02-24",
    end: "2024-03-01",
  });
  assert.deepEqual(periodDates("month", new Date("2026-10-09T00:00:00Z")), {
    start: "2026-10-01",
    end: "2026-10-09",
  });
});
test("usage contract rejects mismatched totals and unsafe integers", () => {
  const amounts = {
    calls: 1,
    settled_calls: 1,
    unsettled_calls: 0,
    estimated_nusd: 1,
    reserved_nusd: 0,
    input_tokens: 3,
    output_tokens: 1,
  };
  const key = {
    id: "unattributed",
    label: "历史未归属",
    configured: false,
    models: ["synthetic"],
  };
  const data = {
    start: "2026-10-01",
    end: "2026-10-01",
    timezone: "UTC",
    currency: "USD",
    cost_basis: "configured_price_estimate",
    count_basis: "recorded_reservations",
    credential_basis: "credential_file_reference",
    summary: { ...amounts },
    daily: [{ day: "2026-10-01", ...amounts }],
    keys: [key],
    by_key: [{ ...key, ...amounts }],
  };
  assert.equal(validUsageReport(data), true);
  data.lifetime = { ...amounts, token_unknown_calls: 0 };
  assert.equal(validUsageReport(data), true);
  data.lifetime.token_unknown_calls = 2;
  assert.equal(validUsageReport(data), false);
  delete data.lifetime;
  data.summary.estimated_nusd = 2;
  assert.equal(validUsageReport(data), false);
  data.summary.estimated_nusd = Number.MAX_SAFE_INTEGER + 1;
  assert.equal(validUsageReport(data), false);
  assert.equal(
    validSystemSettings({
      session_ttl: 900,
      revision: 1,
      timing: "fixed_from_login",
      effective: "next_login",
    }),
    true,
  );
  assert.equal(validSystemSettings({ session_ttl: true, revision: 1 }), false);
});
test("system API keeps filters encoded and settings writes inside the shared request boundary", async () => {
  const requests = [],
    api = createSystemApi(async (...args) => requests.push(args));
  await api.usageReport({
    start: "2026-10-01",
    end: "2026-10-03",
    api_key_id: "x&y",
  });
  assert.equal(
    requests[0][0],
    "/admin/system/usage?start=2026-10-01&end=2026-10-03&api_key_id=x%26y",
  );
  await api.saveSystemSettings({ session_ttl: 900, expected_revision: 0 });
  assert.equal(requests[1][1], "PUT");
  assert.equal(requests[1][3], validSystemSettings);
});

test("unknown model pricing never displays zero as settled cost", async () => {
  const { costText } =
    await import("../../src/fiber_bundle/web/usage_chart.js");
  assert.equal(
    costText({
      calls: 1,
      settled_calls: 0,
      unsettled_calls: 1,
      estimated_nusd: 0,
    }),
    "未知",
  );
  assert.equal(
    costText({
      calls: 2,
      settled_calls: 1,
      unsettled_calls: 1,
      estimated_nusd: 1000000000,
    }),
    "$1.00（部分未知）",
  );
});

test("empty and nano-dollar cost axes have distinct ticks without inflating small real costs", () => {
  for (const cost of [0, 1, 100]) {
    const graph = chartGeometry([{ calls: 1, estimated_nusd: cost }]);
    const labels = [0, 0.5, 1].map((f) => money(Math.round(graph.costMax * f)));
    assert.equal(new Set(labels).size, 3);
    assert.ok(graph.points[0].costY >= graph.top);
  }
});
