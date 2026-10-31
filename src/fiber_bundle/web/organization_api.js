import { createDraftImportApi } from "./draft_import_api.js";
// Transport only; identity and permissions are rechecked by the backend.
export function createOrganizationApi(request) {
  const base = "/admin/organization";
  return {
    drafts: createDraftImportApi(request),
    progress: (scope) =>
      request(base + "/progress?" + new URLSearchParams(scope)),
    list: (scope, offset = 0, q = "", exceptions = true) =>
      request(
        base + "?" + new URLSearchParams({ ...scope, offset, q, exceptions }),
      ),
    cloudFile: (body) => request(base + "/cloud-file", "POST", body),
    repairPreview: (id, body) =>
      request(
        base + "/maintenance/" + encodeURIComponent(id) + "/links/preview",
        "POST",
        body,
      ),
    repairConfirm: (id, body) =>
      request(
        base + "/maintenance/" + encodeURIComponent(id) + "/links/confirm",
        "POST",
        body,
      ),
    maintain: (body) => request(base + "/maintenance", "POST", body),
    maintenanceAction: (id, operation, body) =>
      request(
        base + "/maintenance/" + encodeURIComponent(id) + "/" + operation,
        "POST",
        body,
      ),
    scan: (body) => request(base + "/packages/scan", "POST", body),
    register: (body) => request(base + "/packages", "POST", body),
    policy: (body) => request(base + "/policy", "PUT", body),
    submit: (body) => request(base + "/jobs", "POST", body),
    action: (id, operation, scope) =>
      request(
        base + "/jobs/" + encodeURIComponent(id) + "/" + operation,
        "POST",
        scope,
      ),
    member: (body) => request(base + "/members", "POST", body),
    members: (body) => request(base + "/members/batch", "POST", body),
  };
}
