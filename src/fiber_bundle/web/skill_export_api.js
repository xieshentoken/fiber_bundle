export function createSkillExportApi(request) {
  const base = "/admin/skill-exports";
  return {
    preview: (body) => request(base + "/preview", "POST", body),
    history: (offset = 0) =>
      request(base + "?offset=" + offset, "GET", undefined, Array.isArray),
    detail: (id) => request(base + "/" + encodeURIComponent(id)),
    action: (id, action, sha) =>
      request(base + "/" + encodeURIComponent(id) + "/actions", "POST", {
        action,
        preview_sha256: sha,
      }),
  };
}
