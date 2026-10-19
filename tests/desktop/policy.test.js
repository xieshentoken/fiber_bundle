import { test } from "node:test";
import assert from "node:assert/strict";
import {
  backendURL,
  allowedRequest,
  allowedDownload,
  windowPreferences,
} from "../../desktop/policy.mjs";

test("desktop accepts only an explicit IPv4 loopback root", () => {
  assert.equal(backendURL(), "http://127.0.0.1:8765/");
  for (const url of [
    "https://example.com/",
    "http://localhost/",
    "file:///tmp/a",
    "http://u:p@127.0.0.1/",
    "http://127.0.0.1/admin/",
    "http://127.0.0.1/?token=x",
    "http://127.0.0.1/#x",
  ])
    assert.throws(() => backendURL(url));
});
test("renderer has no Node bridge and denies remote or other loopback APIs", () => {
  assert.equal(windowPreferences.nodeIntegration, false);
  assert.equal(windowPreferences.sandbox, true);
  assert.equal(windowPreferences.contextIsolation, true);
  assert.ok(
    allowedRequest("http://127.0.0.1:8765/admin/session", backendURL()),
  );
  assert.ok(
    allowedRequest("http://127.0.0.1:8765/static/app.js", backendURL()),
  );
  for (const url of [
    "http://127.0.0.1:8000/admin/",
    "https://example.com/",
    "file:///etc/passwd",
    "http://127.0.0.1:8765/private",
    "http://u:p@127.0.0.1:8765/",
  ])
    assert.equal(allowedRequest(url, backendURL()), false);
});

test("desktop downloads only exact local attachment routes without widening navigation", () => {
  const id = "12345678-1234-1234-1234-123456789abc";
  const root = backendURL();
  for (const path of [
    `admin/documents/${id}/source`,
    `admin/agent-connections/${id}/access-skill`,
    `admin/skill-exports/${id}/download`,
    `admin/imports/${id}/source?client_id=${id}`,
    `admin/imports/${id}/expert-package?client_id=${id}`,
    `admin/channels/${id}/intake/batches/${id}/originals/${id}`,
    `admin/channels/${id}/intake/batches/${id}/links/0/assets/source.html`,
    `admin/channels/${id}/intake/batches/${id}/links/1/assets/images/image-01.png`,
  ])
    assert.equal(allowedDownload(root + path, root), true);
  for (const value of [
    root + "admin/session",
    root + `admin/agent-connections/${id}/access-skill?x=1`,
    root + `admin/skill-exports/${id}/download#fragment`,
    root + `admin/skill-exports/not-an-id/download`,
    root + `admin/skill-exports/${id}/preview`,
    root + `admin/documents/${id}/source?x=1`,
    root + `admin/imports/${id}/source`,
    root + `admin/imports/${id}/source?client_id=${id}&client_id=${id}`,
    root + `admin/imports/${id}/source?client_id=${id}#fragment`,
    root + "admin/documents/not-an-id/source",
    root + `admin/channels/${id}/intake/batches/${id}/originals/${id}?x=1`,
    root +
      `admin/channels/${id}/intake/batches/${id}/links/0/assets/private.json`,
    root +
      `admin/channels/${id}/intake/batches/${id}/links/0/assets/images/../secret.png`,
    "http://127.0.0.1:8000/admin/documents/" + id + "/source",
    "https://example.com/admin/documents/" + id + "/source",
  ])
    assert.equal(allowedDownload(value, root), false);
});
