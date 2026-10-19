// Backup HTTP contracts contain status and policy only, never recovery keys.
const object = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const phases = ["capacity", "scanning", "encrypting", "verifying"];
const finite = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0;
const counter = (v) => Number.isSafeInteger(v) && v >= 0;
function validProgress(value) {
  return (
    object(value) &&
    phases.includes(value.phase) &&
    ["files_done", "files_total", "bytes_done", "bytes_total"].every(
      (key) => value[key] === undefined || counter(value[key]),
    ) &&
    (value.updated_at === undefined || finite(value.updated_at)) &&
    (value.complete === undefined || typeof value.complete === "boolean")
  );
}
function jobProgress(job) {
  return (
    (job.progress === undefined || validProgress(job.progress)) &&
    (job.stages === undefined ||
      (object(job.stages) &&
        Object.entries(job.stages).every(
          ([key, value]) => validProgress(value) && key === value.phase,
        ))) &&
    ["started_at", "finished_at"].every(
      (key) => job[key] === undefined || finite(job[key]),
    ) &&
    (job.failure_details == null ||
      (object(job.failure_details) &&
        typeof job.failure_details.section === "string" &&
        typeof job.failure_details.path === "string" &&
        job.failure_details.path.length <= 2048))
  );
}
export function validBackupStatus(value) {
  return (
    object(value) &&
    Number.isInteger(value.revision) &&
    (value.storage_mode === undefined ||
      ["incremental", "complete"].includes(value.storage_mode)) &&
    (value.full_verify_days === undefined ||
      (Number.isInteger(value.full_verify_days) &&
        value.full_verify_days >= 1 &&
        value.full_verify_days <= 366)) &&
    object(value.policy) &&
    typeof value.policy.directory === "string" &&
    typeof value.policy.enabled === "boolean" &&
    typeof value.key_configured === "boolean" &&
    Array.isArray(value.jobs) &&
    value.jobs.every(
      (job) =>
        object(job) &&
        typeof job.id === "string" &&
        [
          "queued",
          "running",
          "succeeded",
          "failed",
          "deleted",
          "deleting",
          "cleanup_interrupted",
        ].includes(job.state) &&
        typeof job.created === "number" &&
        typeof job.pinned === "boolean" &&
        jobProgress(job),
    ) &&
    !("recovery_key" in value) &&
    !("account" in value)
  );
}
export function createBackupAutomationApi(request, pathId) {
  const path = "/admin/backup-automation";
  return {
    backupAutomation: () => request(path, "GET", undefined, validBackupStatus),
    saveBackupAutomation: (body) =>
      request(path, "PUT", body, validBackupStatus),
    runBackup: () => request(path + "/jobs", "POST"),
    cleanupBackups: () => request(path + "/cleanup", "POST"),
    pinBackup: (id, pinned) =>
      request(
        path + "/jobs/" + pathId(id) + "/pin",
        "PUT",
        { pinned },
        validBackupStatus,
      ),
  };
}
