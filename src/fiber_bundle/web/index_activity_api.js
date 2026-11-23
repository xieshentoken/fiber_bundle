// Compact ledger observations only; reading activity never schedules index work.
const indexActivityStates = new Set([
  "observing",
  "queued",
  "building",
  "waiting",
  "blocked",
  "failed",
  "needs_review",
  "ready",
  "unverified",
]);
const record = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const keys = (value, expected) =>
  Object.keys(value).length === expected.length &&
  expected.every((name) => Object.hasOwn(value, name));
const count = (value) =>
  value === null || (Number.isSafeInteger(value) && value >= 0);
const timestamp = (value) => Number.isFinite(value) && value > 0;
const text = (value, maximum) =>
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= maximum &&
  !Array.from(value).some(
    (character) =>
      character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
  );

export function validIndexActivity(value) {
  if (
    !record(value) ||
    !keys(value, ["schema", "observed_at", "vaults"]) ||
    value.schema !== "note-index-activity/v1" ||
    !timestamp(value.observed_at) ||
    !Array.isArray(value.vaults)
  )
    return false;
  const identities = new Set();
  return value.vaults.every((row) => {
    if (
      !record(row) ||
      !keys(row, [
        "vault_id",
        "name",
        "state",
        "pending_paths",
        "needs_attention",
        "updated_at",
        ...(Object.hasOwn(row, "phase") ? ["phase"] : []),
        ...(Object.hasOwn(row, "dependency") ? ["dependency"] : []),
      ]) ||
      !text(row.vault_id, 128) ||
      !text(row.name, 256) ||
      !indexActivityStates.has(row.state) ||
      (row.state === "blocked" && !Object.hasOwn(row, "dependency")) ||
      (Object.hasOwn(row, "phase") &&
        !["observation", "packages"].includes(row.phase)) ||
      (Object.hasOwn(row, "dependency") &&
        !validIndexDependency(row.dependency)) ||
      !count(row.pending_paths) ||
      !count(row.needs_attention) ||
      !(row.updated_at === null || timestamp(row.updated_at)) ||
      identities.has(row.vault_id)
    )
      return false;
    identities.add(row.vault_id);
    return true;
  });
}

export function createIndexActivityApi(request) {
  return {
    indexActivity: ({ signal } = {}) =>
      request(
        "/admin/note-index/activity",
        "GET",
        undefined,
        validIndexActivity,
        { signal, quietConnection: true, retryBusy: false },
      ),
  };
}

export function validIndexDependency(value) {
  return (
    record(value) &&
    value.reason === "placement_reconciliation_required" &&
    ["verify_committed_archives", "review_original_operation"].includes(
      value.recovery,
    ) &&
    Array.isArray(value.operations) &&
    value.operations.length > 0 &&
    value.operations.every(
      (operation) =>
        record(operation) &&
        text(operation.id, 128) &&
        text(operation.kind, 128) &&
        ["applying", "reconcile"].includes(operation.state) &&
        Array.isArray(operation.job_ids) &&
        operation.job_ids.every((id) => text(id, 128)),
    )
  );
}
