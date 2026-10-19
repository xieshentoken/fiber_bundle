// Graph transport uses the existing authenticated HTTP client and fixed operation paths.
export function createGraphApi(request) {
  const base = "/admin/knowledge-graph";
  const scope = (cid, vid) =>
    "?client_id=" +
    encodeURIComponent(cid) +
    "&vault_id=" +
    encodeURIComponent(vid);
  const path = (id) => encodeURIComponent(id);
  return {
    saveSettings: (vid) =>
      request("/admin/vault-catalog/" + path(vid) + "/save-policy"),
    projection: (cid, vid, focus = null) =>
      request(
        base +
          "/projection" +
          scope(cid, vid) +
          (focus ? "&focus=" + path(focus) : ""),
      ),
    nodeSearch: (cid, vid, query) =>
      request(
        base + "/node-search" + scope(cid, vid) + "&query=" + path(query),
        "GET",
        undefined,
        Array.isArray,
      ),
    node: (cid, vid, id) =>
      request(base + "/node" + scope(cid, vid) + "&note_id=" + path(id)),
    detectRuntime: () => request(base + "/runtime/detect", "POST"),
    updateIndex: (body) => request("/admin/note-index/update", "POST", body),
    policy: () => request(base + "/policy"),
    savePolicy: (body) => request(base + "/policy", "PUT", body),
    notes: (cid, vid, query = "", offset = 0) =>
      request(
        base +
          "/notes" +
          scope(cid, vid) +
          "&q=" +
          path(query) +
          "&offset=" +
          offset,
        "GET",
        undefined,
        Array.isArray,
      ),
    suggestions: (cid, vid) => request(base + "/suggestions" + scope(cid, vid)),
    jobs: (cid, vid) =>
      request(
        base + "/jobs" + scope(cid, vid),
        "GET",
        undefined,
        Array.isArray,
      ),
    submit: (body) => request(base + "/jobs", "POST", body),
    jobAction: (id, action, body) =>
      request(base + "/jobs/" + path(id) + "/" + path(action), "POST", body),
    review: (id, body) =>
      request(base + "/suggestions/" + path(id) + "/review", "POST", body),
    intents: (cid, vid) =>
      request(
        base + "/writebacks" + scope(cid, vid),
        "GET",
        undefined,
        Array.isArray,
      ),
    preview: (body) => request(base + "/writebacks", "POST", body),
    intentAction: (id, action, body) =>
      request(
        base + "/writebacks/" + path(id) + "/" + path(action),
        "POST",
        body,
      ),
  };
}
