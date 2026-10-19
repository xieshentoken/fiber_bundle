export function backendURL(value = "http://127.0.0.1:8765/") {
  const url = new URL(value);
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash ||
    (url.port && !/^[0-9]+$/.test(url.port))
  )
    throw new Error("Only a loopback backend root is supported");
  return url.href;
}
export const windowPreferences = Object.freeze({
  nodeIntegration: false,
  contextIsolation: true,
  sandbox: true,
  webSecurity: true,
  allowRunningInsecureContent: false,
  partition: "persist:fiber-admin",
});
export function allowedRequest(value, backend, localFiles = []) {
  if (localFiles.includes(value)) return true;
  try {
    const url = new URL(value),
      origin = new URL(backend).origin;
    return (
      url.origin === origin &&
      ["http:", "blob:"].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      (url.protocol === "blob:" ||
        url.pathname === "/" ||
        url.pathname === "/health" ||
        ["/static/", "/admin/", "/v1/"].some((p) => url.pathname.startsWith(p)))
    );
  } catch {
    return false;
  }
}

// Only these authenticated attachment endpoints may initiate a desktop download.
export function allowedDownload(value, backend) {
  if (!allowedRequest(value, backend)) return false;
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hash) return false;
  const uuid = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
  if (
    new RegExp(`^/admin/agent-connections/${uuid}/access-skill$`).test(
      url.pathname,
    ) ||
    new RegExp(`^/admin/skill-exports/${uuid}/download$`).test(url.pathname)
  )
    return url.search === "";
  if (new RegExp(`^/admin/documents/${uuid}/source$`).test(url.pathname))
    return url.search === "";
  const intake = `/admin/channels/${uuid}/intake/batches/${uuid}`;
  if (new RegExp(`^${intake}/originals/${uuid}$`).test(url.pathname))
    return url.search === "";
  if (
    new RegExp(
      `^${intake}/links/[0-9]+/assets/(source\\.html|captions\\.srt|provenance\\.json|note\\.md|images/[a-zA-Z0-9_-]+\\.(png|jpg|jpeg|gif|webp))$`,
    ).test(url.pathname)
  )
    return url.search === "";
  if (
    !new RegExp(`^/admin/imports/${uuid}/(source|expert-package)$`).test(
      url.pathname,
    )
  )
    return false;
  const entries = [...url.searchParams];
  return (
    entries.length === 1 &&
    entries[0][0] === "client_id" &&
    new RegExp(`^${uuid}$`).test(entries[0][1])
  );
}
