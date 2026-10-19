// Model configuration uses the existing authenticated, cancellable HTTP boundary.
const record = (value) =>
  value && typeof value === "object" && !Array.isArray(value);
export function createModelsApi(request) {
  const path = (id) => "/admin/models/" + encodeURIComponent(id);
  return {
    catalog: () =>
      request(
        "/admin/models",
        "GET",
        undefined,
        (v) =>
          record(v) &&
          Array.isArray(v.models) &&
          Array.isArray(v.credentials) &&
          Array.isArray(v.providers),
      ),
    discover: (body, signal) =>
      request(
        "/admin/models/discover",
        "POST",
        body,
        (v) => record(v) && Array.isArray(v.models),
        { signal },
      ),
    save: (body) => request("/admin/models", "POST", body),
    control: (id, body) => request(path(id) + "/control", "POST", body),
    test: (id, revision) =>
      request(path(id) + "/test", "POST", {
        expected_revision: revision,
        approve_tokens: true,
      }),
    testStatus: (id) =>
      request("/admin/models/tests/" + encodeURIComponent(id)),
    runtime: () => request("/admin/models/runtime"),
    prepare: () => request("/admin/models/runtime/prepare", "POST", {}),
  };
}
