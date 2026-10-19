import test from "node:test";
import assert from "node:assert/strict";
import {
  validBackupStatus,
  createBackupAutomationApi,
} from "../../src/fiber_bundle/web/backup_automation_api.js";
test("backup status accepts interrupted cleanup without exposing secrets", () => {
  const value = {
    revision: 0,
    policy: { enabled: false, directory: "" },
    key_configured: false,
    jobs: [],
  };
  assert(validBackupStatus(value));
  for (const state of [
    "queued",
    "running",
    "succeeded",
    "failed",
    "deleted",
    "deleting",
    "cleanup_interrupted",
  ]) {
    value.jobs = [{ id: "synthetic", state, created: 1, pinned: false }];
    assert(validBackupStatus(value));
  }
  assert(!validBackupStatus({ ...value, recovery_key: "must-not-appear" }));
  value.jobs[0].state = "unknown";
  assert(!validBackupStatus(value));
});
test("backup operations use only fixed admin contracts", async () => {
  const calls = [];
  const api = createBackupAutomationApi(
    async (...args) => calls.push(args),
    encodeURIComponent,
  );
  await api.backupAutomation();
  await api.runBackup();
  await api.pinBackup("synthetic", true);
  await api.cleanupBackups();
  assert.deepEqual(
    calls.map((v) => v[0]),
    [
      "/admin/backup-automation",
      "/admin/backup-automation/jobs",
      "/admin/backup-automation/jobs/synthetic/pin",
      "/admin/backup-automation/cleanup",
    ],
  );
});

test("storage and verification settings accept legacy status but reject invalid new values", () => {
  const value = {
    revision: 0,
    policy: { enabled: false, directory: "" },
    key_configured: false,
    jobs: [],
  };
  assert(validBackupStatus(value));
  assert(
    validBackupStatus({
      ...value,
      storage_mode: "incremental",
      full_verify_days: 7,
    }),
  );
  assert(!validBackupStatus({ ...value, storage_mode: "unknown" }));
  assert(!validBackupStatus({ ...value, full_verify_days: 0 }));
  assert(!validBackupStatus({ ...value, full_verify_days: true }));
});

import { architecture } from "../../src/fiber_bundle/web/home_model.js";
test("homepage reports authenticated backup, waiting and failure accurately", () => {
  for (const [state, tone] of [
    ["backup_verified", "success"],
    ["backup_failed", "error"],
    ["backup_running", "waiting"],
    ["backup_cleanup_pending", "error"],
  ]) {
    const result = architecture({ backups: { value: [{ state }] } });
    assert.equal(result.nodes.backup.tone, tone);
  }
});

import {
  homeStatus,
  criticalAlert,
} from "../../src/fiber_bundle/web/home_model.js";
test("a bounded snapshot wait is not a database failure", () => {
  const records = {
    backend: { error: { status: 503, code: "backup_snapshot_busy" } },
    vaults: { error: { status: 503, code: "backup_snapshot_busy" } },
  };
  assert.equal(homeStatus(records).tone, "waiting");
  assert.equal(criticalAlert(records), null);
});

test("backup progress counters reject malformed external status", () => {
  const job = {
    id: "synthetic",
    state: "running",
    created: 1,
    pinned: false,
    progress: { phase: "scanning", files_done: 2, bytes_done: 10 },
    stages: {
      capacity: {
        phase: "capacity",
        files_done: 2,
        bytes_done: 10,
        complete: true,
      },
    },
  };
  const status = {
    revision: 0,
    policy: { enabled: false, directory: "" },
    key_configured: false,
    jobs: [job],
  };
  assert(validBackupStatus(status));
  job.stages.capacity = null;
  assert(!validBackupStatus(status));
  job.stages.capacity = { phase: "capacity", files_done: 2 };
  job.progress.bytes_done = -1;
  assert(!validBackupStatus(status));
  job.progress.bytes_done = 1;
  job.stages.capacity.phase = "encrypting";
  assert(!validBackupStatus(status));
});
