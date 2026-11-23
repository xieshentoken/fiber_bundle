// Authenticated transport only; the backend owns authorization and file evidence.
export function createInboxReviewApi(request) {
  const base = "/admin/organization/inbox-review";
  const operation = (id, action, body) =>
    request(
      `${base}/operations/${encodeURIComponent(id)}/${action}`,
      "POST",
      body,
      operationResponse,
    );
  return {
    inspect: (body) =>
      request(base + "/inspect", "POST", body, inspectionResponse),
    preview: (body) =>
      request(base + "/preview", "POST", body, operationResponse),
    apply: (id, body) => operation(id, "apply", body),
    restorePreview: (id, body) => operation(id, "restore-preview", body),
    history: (scope) =>
      request(
        base + "/history?" + new URLSearchParams(scope),
        "GET",
        undefined,
        (value) =>
          record(value) &&
          Array.isArray(value.operations) &&
          value.operations.every(operationResponse),
      ),
  };
}

// Retain only bounded, relative diagnostics for this workflow's error surface.
export function readInboxReviewDiagnostic(value) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    typeof value.reason !== "string" ||
    !/^[a-z][a-z0-9_]{0,120}$/.test(value.reason)
  )
    return null;
  const result = { reason: value.reason };
  if (
    typeof value.path === "string" &&
    value.path.length <= 2048 &&
    !value.path.startsWith("/") &&
    !value.path.split("/").includes("..")
  )
    result.path = value.path;
  if (
    typeof value.next_step === "string" &&
    /^[a-z][a-z0-9_]{0,120}$/.test(value.next_step)
  )
    result.next_step = value.next_step;
  return result;
}

const record = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value) => typeof value === "string";
const line = (value) =>
  record(value) &&
  Number.isSafeInteger(value.line) &&
  value.line > 0 &&
  text(value.before) &&
  text(value.after);
export function operationResponse(value) {
  return (
    record(value) &&
    text(value.operation_id) &&
    text(value.path) &&
    text(value.plan_sha256) &&
    ["prepared", "applying", "reconcile", "completed"].includes(value.state) &&
    [
      "quarantine_empty",
      "unlink_missing",
      "restore_quarantine",
      "restore_links",
    ].includes(value.action) &&
    Array.isArray(value.changes) &&
    value.changes.every(line) &&
    ["can_apply", "can_restore", "can_reconcile"].every(
      (key) => typeof value[key] === "boolean",
    )
  );
}
export function inspectionResponse(value) {
  return (
    record(value) &&
    text(value.path) &&
    ["empty", "title_only", "content", "unreadable"].includes(
      value.classification,
    ) &&
    (value.byte_count === null ||
      (Number.isSafeInteger(value.byte_count) && value.byte_count >= 0)) &&
    record(value.text) &&
    Array.isArray(value.text.lines) &&
    value.text.lines.every(
      (row) =>
        record(row) &&
        Number.isSafeInteger(row.number) &&
        row.number > 0 &&
        text(row.text),
    ) &&
    typeof value.text.truncated === "boolean" &&
    record(value.actions) &&
    ["quarantine_empty", "unlink_missing"].every(
      (key) => typeof value.actions[key] === "boolean",
    ) &&
    Array.isArray(value.links) &&
    value.links.every(
      (item) => line(item) && text(item.id) && text(item.target),
    ) &&
    Array.isArray(value.publication) &&
    value.publication.every(
      (item) => record(item) && text(item.publication_id) && text(item.state),
    ) &&
    Array.isArray(value.issues) &&
    value.issues.every((item) => record(item) && text(item.code))
  );
}
