// Explicit named operations over the existing authenticated management transport.
export { agentErrorText } from "./agent_labels.js";
export function createAgentApi(request) {
  const base = "/admin/agent-connections";
  const path = (id) => base + "/" + encodeURIComponent(id);
  return {
    list: () => request(base, "GET", undefined, Array.isArray),
    catalog: () => request(base + "/catalog"),
    detail: (id) => request(path(id)),
    create: (body) => request(base, "POST", body),
    save: (id, body) => request(path(id), "PATCH", body),
    control: (id, action, body) =>
      request(path(id) + "/" + action, "POST", body),
    start: (id, body) => request(path(id) + "/operations", "POST", body),
    status: (id, oid) =>
      request(path(id) + "/operations/" + encodeURIComponent(oid)),
    cancel: (id, oid, body) =>
      request(
        path(id) + "/operations/" + encodeURIComponent(oid) + "/cancel",
        "POST",
        body,
      ),
    history: (id, offset = 0) =>
      request(
        path(id) + "/operations?offset=" + offset,
        "GET",
        undefined,
        Array.isArray,
      ),
    knowledgeCatalog: (id, body) =>
      request(path(id) + "/knowledge-catalog", "POST", body),
    knowledgeEvidence: (id, body) =>
      request(path(id) + "/knowledge-evidence", "POST", body),
    knowledgeExpert: (id, body) =>
      request(path(id) + "/knowledge-expert", "POST", body),
    accessMaterials: (id) => request(path(id) + "/access-materials"),
    previewKnowledge: (id, body) =>
      request(path(id) + "/knowledge-preview", "POST", body),
    export: (id) => request(path(id) + "/configuration-export"),
  };
}
