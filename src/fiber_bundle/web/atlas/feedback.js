import { requestErrors } from "../request_errors.js";
import { translate } from "../i18n/index.js";
// Toasts, busy buttons and error wording. Presentation never decides policy.
import { h, icon } from "./dom.js";
import { lang, strings } from "./i18n.js";

const t = strings({
  zh: {
    dismiss: "关闭通知",
    working: "处理中…",
    stillWorking: "仍在处理，请勿重复提交…",
  },
  en: {
    dismiss: "Dismiss notification",
    working: "Working…",
    stillWorking: "Still working — please don't submit again…",
  },
});
// English wording by stable error code or HTTP status. Chinese uses the backend-facing
// text produced by the shared API client, which is already specific.
const englishCodes = {
  network:
    "Can't reach the local backend. Check that it is running, then retry.",
  timeout: "The request timed out. Please retry shortly.",
  cancelled: "Request cancelled.",
  ...Object.fromEntries(
    Object.entries(requestErrors).map(([code, pair]) => [code, pair[1]]),
  ),
  note_index_update_required:
    "Notes changed; the full-text index must be updated before content is delivered.",
  note_index_match_limit:
    "Too many matches. Narrow the vault scope or use more specific keywords.",
  backup_unclassified_state:
    "The state directory holds unclassified data; backup preparation was blocked. Check the scope; do not delete originals.",
};
const englishStatus = {
  401: "Session expired. Please sign in again.",
  403: "Request not permitted. Refresh the session and retry.",
  404: "Resource unavailable or permission revoked. Please refresh.",
  409: "Conflict with the current state. Refresh and review.",
  413: "The request is too large.",
  422: "Input was rejected. Check the values and permissions.",
  429: "Too many attempts. Please wait and retry.",
  503: "Backend storage is unavailable. Check the local backend.",
};
export function errorText(error) {
  const message = error?.message || String(error || "");
  if (lang() === "zh") return { title: message || "操作未完成。", detail: "" };
  const title =
    englishCodes[error?.code] ||
    englishStatus[error?.status] ||
    "The operation did not complete.";
  const translated = translate(message);
  const detail =
    message && translated !== title && !englishCodes[error?.code]
      ? /[\u3400-\u9fff]/.test(translated)
        ? error?.code || ""
        : translated
      : "";
  return { title, detail };
}

let region;
function host() {
  if (region?.isConnected) return region;
  region = h("div.a-toasts", { role: "status", "aria-live": "polite" });
  document.body.append(region);
  return region;
}
// toast(text, { tone: "success"|"error"|"info"|"warning", detail, timeout })
export function toast(text, options = {}) {
  if (!text) return () => {};
  const tone = options.tone || "info";
  const node = h(
    "div.a-toast",
    { dataset: { tone }, role: tone === "error" ? "alert" : null },
    icon(
      { success: "check", error: "alert", warning: "alert", info: "info" }[
        tone
      ],
    ),
    h(
      "div.a-toast-body",
      h("p.a-toast-title", text),
      options.detail ? h("p.a-toast-detail", options.detail) : null,
    ),
    h(
      "button.a-icon-button",
      {
        type: "button",
        "aria-label": t("dismiss"),
        onclick: () => close(),
      },
      icon("close"),
    ),
  );
  let timer;
  function close() {
    clearTimeout(timer);
    node.remove();
  }
  const list = host();
  list.append(node);
  while (list.children.length > 4) list.firstElementChild.remove();
  const timeout = options.timeout ?? (tone === "error" ? 0 : 5000);
  if (timeout) timer = setTimeout(close, timeout);
  return close;
}
export function toastError(error) {
  if (error?.code === "cancelled") return;
  const { title, detail } = errorText(error);
  toast(title, { tone: "error", detail });
}

// Run `work` while the button shows progress; errors appear inline and as a toast.
// `work` may return false when the user declined, which is not an error.
export async function run(button, work) {
  if (button.getAttribute("aria-busy") === "true") return undefined;
  const inline = button.parentElement?.querySelector(
    `:scope > .a-inline-error[data-for="${button.dataset.runId || ""}"]`,
  );
  inline?.remove();
  button.setAttribute("aria-busy", "true");
  button.disabled = true;
  const slow = setTimeout(() => {
    button.title = t("stillWorking");
  }, 8000);
  try {
    return await work();
  } catch (error) {
    toastError(error);
    if (error?.code !== "cancelled") {
      button.dataset.runId ||= String(Math.random()).slice(2, 10);
      button.insertAdjacentElement(
        "afterend",
        h(
          "p.a-inline-error",
          { dataset: { for: button.dataset.runId }, role: "alert" },
          errorText(error).title,
        ),
      );
    }
    return undefined;
  } finally {
    clearTimeout(slow);
    button.title = "";
    button.removeAttribute("aria-busy");
    button.disabled = button.dataset.locked === "true";
  }
}
// Convenience: a button whose click runs `work` through run().
export function actionButton(spec, label, work, props = {}) {
  const button = h(spec, { type: "button", ...props }, label);
  button.addEventListener("click", () => run(button, work));
  return button;
}
export const workingText = () => t("working");
