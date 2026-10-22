import { setText, uiText } from "./i18n/index.js";
// Recoverable text stays in this tab's memory while authentication is renewed.
// Passwords, keys, tokens, file selections and server-rendered records are excluded.
import { hasUnsavedChanges } from "./unsaved_changes.js";
function recoverable(workspace) {
  return [
    ...workspace.querySelectorAll(
      '[data-draft="tracked"] input, [data-draft="tracked"] select, [data-draft="tracked"] textarea, input[data-draft="tracked"], select[data-draft="tracked"], textarea[data-draft="tracked"]',
    ),
  ].filter(
    (field) =>
      !["password", "file", "hidden"].includes(field.type) &&
      (field.tagName === "TEXTAREA" ||
        ["text", "search", "url", "email", "tel", "number"].includes(
          field.type,
        )) &&
      !!field.id &&
      !field.readOnly &&
      !field.closest('[data-draft="ignore"]') &&
      !/password|secret|token|api.?key|credential|密码|密钥|令牌|凭据/i.test(
        `${field.id} ${field.name} ${field.dataset?.sessionKey || ""}`,
      ) &&
      !/\bsk-[A-Za-z0-9_-]{16,}/.test(field.value),
  );
}
export function createSessionExpiry(session, workspace, warning) {
  let saved = [],
    savedPage = "";
  return {
    update(now = Date.now() / 1000) {
      const remaining = session.expiresAt - now;
      warning.hidden = !session.csrf || remaining > 300 || remaining <= 0;
      if (!warning.hidden) {
        const minutes = Math.max(1, Math.ceil(remaining / 60));
        const text = `登录将在 ${minutes} 分钟内到期。请及时保存当前内容；到期后可重新登录继续。`;
        if (warning.textContent !== text) setText(warning, uiText(text));
      }
      return session.expired(now);
    },
    capture() {
      saved = [];
      let size = 0;
      const panel = workspace.querySelector(":scope > section:not([hidden])");
      savedPage = panel?.id || "";
      if (!panel) return;
      for (const [index, field] of recoverable(panel).entries()) {
        if (!hasUnsavedChanges(field)) continue;
        size += field.value.length;
        if (size > 65536) break;
        saved.push({
          index,
          id: field.id,
          key: field.dataset?.sessionKey,
          ordinal: recoverable(panel)
            .filter(
              (other) =>
                other.dataset?.sessionKey === field.dataset?.sessionKey,
            )
            .indexOf(field),
          name: field.name,
          value: field.value,
        });
      }
      warning.hidden = true;
    },
    restore() {
      const panel = workspace.querySelector(":scope > section:not([hidden])");
      if (!panel || panel.id !== savedPage) return false;
      const fields = recoverable(panel);
      let restored = false;
      for (const item of saved) {
        const field = item.key
          ? fields.filter((field) => field.dataset?.sessionKey === item.key)[
              item.ordinal
            ]
          : item.id
            ? fields.find((field) => field.id === item.id)
            : fields[item.index];
        if (!field || field.name !== item.name) continue;
        field.value = item.value;
        field.dispatchEvent(new window.Event("input", { bubbles: true }));
        restored = true;
      }
      saved = [];
      savedPage = "";
      return restored;
    },
    discard() {
      saved = [];
      savedPage = "";
      warning.hidden = true;
    },
  };
}
