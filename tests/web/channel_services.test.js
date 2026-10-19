import test from "node:test";
import assert from "node:assert/strict";
import {
  createChannelServicesApi,
  serviceErrorText,
} from "../../src/fiber_bundle/web/channel_services_api.js";
import { channelPurpose } from "../../src/fiber_bundle/web/channel_services.js";
test("service purpose preserves mixed legacy entries and explicit roles", () => {
  assert.equal(
    channelPurpose({ configuration: { settings: { add: true } } }),
    "both",
  );
  assert.equal(
    channelPurpose({ configuration: { settings: { add: false } } }),
    "query",
  );
  assert.equal(channelPurpose({ services: { purpose: "intake" } }), "intake");
});
test("role API targets one entry and role with revision; rejects invalid actions", async () => {
  const calls = [],
    api = createChannelServicesApi((...args) => {
      calls.push(args);
      return Promise.resolve({});
    });
  await api.channelServicesActivate("a/b", 4);
  await api.channelServiceAction("a/b", "query", "pause", 3);
  await api.channelServiceAction("a/b", "intake", "update", 5);
  assert.deepEqual(calls, [
    [
      "/admin/channels/a%2Fb/services/activate",
      "POST",
      { expected_channel_revision: 4 },
    ],
    [
      "/admin/channels/a%2Fb/services/query/pause",
      "POST",
      { expected_revision: 3 },
    ],
    [
      "/admin/channels/a%2Fb/services/intake/update",
      "POST",
      { expected_revision: 5 },
    ],
  ]);
  assert.throws(() => api.channelServiceAction("a", "all", "pause", 1));
  assert.throws(() => api.channelServiceAction("a", "query", "restart-all", 1));
  assert.equal(calls.length, 3);
  assert.match(serviceErrorText("channel_service_revision_conflict"), /刷新/);
  assert.equal(serviceErrorText("unknown"), null);
});
