import test from "node:test";
import assert from "node:assert/strict";
import { createOrganizationApi } from "../../src/fiber_bundle/web/organization_api.js";

test("maintenance mode and confirmation hash use dedicated endpoints", async () => {
  const calls = [],
    request = async (...args) => {
      calls.push(args);
      return { id: "plan" };
    };
  const api = createOrganizationApi(request),
    scope = { client_id: "client", vault_id: "vault" };
  for (const mode of ["quick", "additive", "deep"])
    await api.maintain({ ...scope, mode, new_mocs: [] });
  await api.maintenanceAction("job/one", "confirm", {
    ...scope,
    plan_sha256: "frozen",
  });
  assert.equal(calls[0][0], "/admin/organization/maintenance");
  assert.deepEqual(
    calls.slice(0, 3).map((c) => c[2].mode),
    ["quick", "additive", "deep"],
  );
  assert.equal(
    calls[3][0],
    "/admin/organization/maintenance/job%2Fone/confirm",
  );
  assert.equal(calls[3][2].plan_sha256, "frozen");
  assert(calls.every((c) => c[1] === "POST"));
});
