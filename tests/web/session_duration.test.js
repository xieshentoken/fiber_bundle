import test from "node:test";
import assert from "node:assert/strict";
import { createSession } from "../../src/fiber_bundle/web/session.js";
import { validSystemSettings } from "../../src/fiber_bundle/web/system_api.js";
import { createSessionExpiry } from "../../src/fiber_bundle/web/session_expiry.js";

test("permanent session restores, never expires, and can be cleared", () => {
  const session = createSession();
  session.accept({ csrf: "synthetic-session", expires_at: 0 });
  assert.equal(session.csrf, "synthetic-session");
  assert.equal(session.expired(1e12), false);
  const warning = { hidden: false };
  const expiry = createSessionExpiry(session, {}, warning);
  assert.equal(expiry.update(1e12), false);
  assert.equal(warning.hidden, true);
  session.clear();
  assert.equal(session.csrf, "");
  for (const value of [-1, null, Infinity, "0", undefined]) {
    assert.throws(() =>
      session.accept({ csrf: "synthetic", expires_at: value }),
    );
  }
});

test("login policy validates extended and permanent durations", () => {
  const policy = {
    revision: 1,
    timing: "fixed_from_login",
    effective: "next_login",
  };
  for (const session_ttl of [0, 900, 86400, 604800, 1209600, 2592000])
    assert.equal(validSystemSettings({ ...policy, session_ttl }), true);
  for (const session_ttl of [-1, 2592001, true, null, "0", 1.5])
    assert.equal(validSystemSettings({ ...policy, session_ttl }), false);
});
