// Session secrets stay in memory. No DOM, browser storage, or HTTP dependencies.
export function createSession() {
  let csrf = "",
    expiresAt = 0,
    revision = 0;
  return {
    get csrf() {
      return csrf;
    },
    get expiresAt() {
      return expiresAt;
    },
    get revision() {
      return revision;
    },
    accept(value) {
      if (
        typeof value?.csrf !== "string" ||
        !value.csrf ||
        !Number.isFinite(value.expires_at) ||
        value.expires_at <= 0
      ) {
        throw new Error("后台会话响应无效。");
      }
      csrf = value.csrf;
      expiresAt = value.expires_at;
      revision++;
    },
    clear() {
      csrf = "";
      expiresAt = 0;
      revision++;
    },
    expired(now = Date.now() / 1000) {
      return expiresAt > 0 && now >= expiresAt;
    },
  };
}
