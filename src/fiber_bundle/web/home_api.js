import { validUsageReport } from "./system_api.js";
// Fixed read-only home contracts. No arbitrary URLs or business writes.
export function createHomeApi(request) {
  const paths = {
    channels: ["/admin/channels/overview", "channels"],
    backend: ["/admin/backend"],
    executions: ["/admin/inventory/executions", "runs"],
    vaults: ["/admin/vaults", null],
    clients: ["/admin/clients", null],
    batches: ["/admin/batches/pending", null],
    documents: ["/admin/documents", "tasks"],
    media: ["/admin/media", "tasks"],
    videos: ["/admin/video-processing", null],
    articles: ["/admin/articles", "tasks"],
    agents: ["/admin/agent-connections", null],
    backups: ["/admin/backup-automation/overview", null],
    query: ["/admin/query/policy"],
    source: ["/admin/source-policy"],
    components: ["/admin/inventory/components"],
    usage: ["/admin/system/usage"],
    index: ["/admin/note-index/summary", "vaults"],
    imports: ["/admin/imports", null],
    expert: ["/admin/imports/expert-commands", null],
    automatic: ["/admin/vault-catalog/", null],
  };
  return (name, params = {}, signal) => {
    const route = paths[name];
    if (!route) throw new Error("未知首页读取合同");
    const allowed =
      name === "usage"
        ? ["start", "end", "api_key_id"]
        : ["imports", "expert", "videos"].includes(name)
          ? ["client_id", "vault_id"]
          : name === "automatic"
            ? ["vault_id"]
            : name === "index"
              ? ["offset", "limit"]
              : [];
    if (Object.keys(params).some((key) => !allowed.includes(key)))
      throw new Error("首页读取范围无效");
    if (
      ["imports", "expert", "videos"].includes(name) &&
      (!params.client_id || !params.vault_id)
    )
      throw new Error("先选择身份和知识库");
    let path = route[0];
    if (name === "automatic") {
      if (!params.vault_id) throw new Error("先选择知识库");
      path += encodeURIComponent(params.vault_id) + "/automatic-jobs";
    } else if (Object.keys(params).length)
      path += "?" + new URLSearchParams(params);
    const shape = (value) =>
      route[1] === null
        ? Array.isArray(value)
        : value !== null &&
          typeof value === "object" &&
          !Array.isArray(value) &&
          (!route[1] || Array.isArray(value[route[1]]));
    const validate = (value) => {
      if (!shape(value)) return false;
      if (name === "usage") return validUsageReport(value);
      const rows =
        route[1] === null ? value : route[1] ? value[route[1]] : null;
      if (
        rows &&
        !rows.every(
          (r) => r !== null && typeof r === "object" && !Array.isArray(r),
        )
      )
        return false;
      if (name === "executions")
        return value.runs.every(
          (r) =>
            typeof r.id === "string" &&
            typeof r.state === "string" &&
            Array.isArray(r.calls) &&
            r.calls.every((c) => c !== null && typeof c === "object"),
        );
      if (["clients", "vaults", "channels"].includes(name))
        return rows.every(
          (r) =>
            typeof r.id === "string" &&
            typeof r.name === "string" &&
            (name !== "clients" || Array.isArray(r.grants)),
        );
      return true;
    };
    return request(path, "GET", undefined, validate, { signal });
  };
}
