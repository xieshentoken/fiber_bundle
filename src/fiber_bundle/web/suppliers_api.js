// Named supplier transport. No credentials or business decisions in the view.
export function createSupplierApi(request, pathId = encodeURIComponent) {
  const scope = (client, vault) =>
    `client_id=${pathId(client)}&vault_id=${pathId(vault)}`;
  const base = "/admin/suppliers";
  const object = (v) =>
    v !== null && typeof v === "object" && !Array.isArray(v);
  return {
    list: (client, vault, signal) =>
      request(`${base}?${scope(client, vault)}`, "GET", undefined, object, {
        signal,
      }),
    preview: (data) => request(`${base}/legacy/preview`, "POST", data, object),
    importLegacy: (data) =>
      request(`${base}/legacy/import`, "POST", data, object),
    policy: (data) => request(`${base}/policy`, "POST", data, object),
    manual: (data) => request(`${base}/manual`, "POST", data, object),
    document: (data) => request(`${base}/document`, "POST", data, object),
    job: (id, action, data) =>
      request(
        `${base}/jobs/${pathId(id)}/${pathId(action)}`,
        "POST",
        data,
        object,
      ),
    correct: (data) => request(`${base}/correction`, "POST", data, object),
    evidence: (client, vault, id) =>
      request(
        `${base}/evidence/${pathId(id)}?${scope(client, vault)}`,
        "GET",
        undefined,
        object,
      ),
    originalUrl: (client, vault, id) =>
      `${base}/original/${pathId(id)}?${scope(client, vault)}`,
    original: (client, vault, id) =>
      request(
        `${base}/original/${pathId(id)}?${scope(client, vault)}`,
        "GET",
        undefined,
        null,
      ),
    markdown: (client, vault, id) =>
      request(
        `${base}/markdown/${pathId(id)}?${scope(client, vault)}`,
        "GET",
        undefined,
        object,
      ),
    query: (data) => request(`${base}/query`, "POST", data, object),
    compare: (data) => request(`${base}/compare`, "POST", data, object),
    mocs: (data) =>
      request(`${base}/classification-preview`, "POST", data, object),
  };
}
