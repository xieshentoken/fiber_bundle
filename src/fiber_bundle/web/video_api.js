// Video operations remain admin-authenticated and never start work on reads.
export function createVideoApi(request) {
  const base = "/admin/video-processing";
  return {
    settings: () => request(base + "/settings"),
    configure: (body) => request(base + "/settings", "PUT", body),
    skill: () => request(base + "/skill"),
    configureSkill: (body) => request(base + "/skill", "PUT", body),
    list: (client, vault) =>
      request(
        base +
          "?client_id=" +
          encodeURIComponent(client) +
          "&vault_id=" +
          encodeURIComponent(vault),
        "GET",
        undefined,
        Array.isArray,
      ),
    createLink: (body) => request(base + "/links", "POST", body),
    linkAction: (id, client, operation) =>
      request(base + "/links/" + encodeURIComponent(id) + "/actions", "POST", {
        client_id: client,
        operation,
      }),
    create: (body) => request(base, "POST", body),
    action: (id, client, operation) =>
      request(base + "/" + encodeURIComponent(id) + "/actions", "POST", {
        client_id: client,
        operation,
      }),
  };
}
