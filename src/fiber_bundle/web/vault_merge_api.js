// Explicit merge previews are read-only. Freeze records an intent without copying data.
const record = (v) => v && typeof v === "object" && !Array.isArray(v);
const id = encodeURIComponent;
export function createVaultMergeApi(request) {
  const state = (v) =>
    record(v) && typeof v.id === "string" && typeof v.state === "string";
  return {
    list: () =>
      request(
        "/admin/vault-merges",
        "GET",
        undefined,
        (v) => Array.isArray(v) && v.every(state),
      ),
    start: (identity, body) =>
      request(`/admin/vault-merges/${id(identity)}/start`, "POST", body, state),
    action: (identity, action, plan_sha256) => {
      if (!["cancel", "resume", "rollback"].includes(action))
        throw new Error("不支持的合并操作");
      return request(
        `/admin/vault-merges/${id(identity)}/${action}`,
        "POST",
        { plan_sha256 },
        state,
      );
    },
    inventory: (vault, client) =>
      request(
        `/admin/vault-merges/inventory/${id(vault)}?client_id=${id(client)}`,
        "GET",
        undefined,
        (v) =>
          record(v) &&
          Array.isArray(v.files) &&
          v.files.every(
            (f) => typeof f.path === "string" && typeof f.managed === "boolean",
          ),
      ),
    preview: (body) =>
      request(
        "/admin/vault-merges/preview",
        "POST",
        body,
        (v) =>
          record(v) &&
          typeof v.plan_sha256 === "string" &&
          typeof v.ready === "boolean" &&
          Array.isArray(v.files) &&
          Array.isArray(v.conflicts),
      ),
    freeze: (selection, plan_sha256) =>
      request(
        "/admin/vault-merges/freeze",
        "POST",
        { selection, plan_sha256 },
        (v) => record(v) && typeof v.id === "string" && v.state === "prepared",
      ),
  };
}
