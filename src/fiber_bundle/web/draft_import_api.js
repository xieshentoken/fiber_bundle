// Transport for external Draft tasks; selecting a folder never starts a confirmed import.
export function createDraftImportApi(request) {
  const base = "/admin/draft-imports";
  const query = (scope) => new URLSearchParams(scope);
  return {
    options: async () => {
      const [clients, vaults] = await Promise.all([
        request("/admin/clients", "GET", undefined, Array.isArray),
        request("/admin/vaults", "GET", undefined, Array.isArray),
      ]);
      return { clients, vaults };
    },
    create: (body) => request(base, "POST", body),
    list: (scope) => request(base + "?" + query(scope)),
    get: (id, scope) =>
      request(base + "/" + encodeURIComponent(id) + "?" + query(scope)),
    confirm: (id, body) =>
      request(base + "/" + encodeURIComponent(id) + "/confirm", "POST", body),
    action: (id, operation, scope) =>
      request(
        base + "/" + encodeURIComponent(id) + "/" + operation,
        "POST",
        scope,
      ),
  };
}
