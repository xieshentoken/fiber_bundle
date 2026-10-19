import { setText, uiText } from "./i18n/index.js";
// A backend replacement is a reason to offer reload, never to discard an open draft.
export function createUpdateNotice(
  host,
  reload = () => window.location.reload(),
) {
  let initial = null;
  const notice = document.createElement("div");
  notice.className = "ui-update-notice";
  notice.hidden = true;
  notice.setAttribute("role", "status");
  const text = document.createElement("span");
  setText(
    text,
    uiText("后台已更新。保存当前草稿后，重新载入界面以使用新版本。"),
  );
  const button = document.createElement("button");
  button.type = "button";
  setText(button, uiText("重新载入界面"));
  button.addEventListener("click", reload);
  notice.append(text, button);
  host.prepend(notice);
  return {
    observe(health) {
      const current = health?.build?.build_id;
      if (typeof current !== "string" || !current) return;
      initial ??= current;
      notice.hidden = current === initial;
    },
  };
}
