import test from "node:test";
import assert from "node:assert/strict";
import { createOrganizationApi } from "../../src/fiber_bundle/web/organization_api.js";

test("organization commands use authenticated fixed routes and preserve explicit scope", async () => {
  const calls = [];
  const api = createOrganizationApi(async (...args) => {
    calls.push(args);
    return {};
  });
  const scope = { client_id: "c", vault_id: "v" };
  await api.list(scope, 100, "热管理", false);
  await api.submit({ ...scope, target: "Inbox", preview: true });
  await api.action("job/a", "confirm", scope);
  await api.member({
    ...scope,
    entity_id: "expert:x",
    moc_id: "m",
    action: "unlink",
  });
  assert(calls[0][0].includes("offset=100"));
  assert(calls[0][0].includes("exceptions=false"));
  assert.equal(calls[1][1], "POST");
  assert.equal(calls[2][0], "/admin/organization/jobs/job%2Fa/confirm");
  assert.equal(calls[3][2].action, "unlink");
  await api.progress(scope);
  assert.equal(
    calls[4][0],
    "/admin/organization/progress?client_id=c&vault_id=v",
  );
  await api.members({
    ...scope,
    entity_id: "note:n",
    moc_ids: ["a", "b"],
    action: "link",
  });
  assert.equal(calls[5][0], "/admin/organization/members/batch");
  assert.deepEqual(calls[5][2].moc_ids, ["a", "b"]);
  await api.scan(scope);
  assert.deepEqual(calls[6], ["/admin/organization/packages/scan", "POST", scope]);
});
