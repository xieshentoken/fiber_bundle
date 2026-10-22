// Modal confirmation and side sheets. A dialog never submits anything by itself;
// callers act only on an explicit `true` from confirm().
import { h, icon } from "./dom.js";
import { strings } from "./i18n.js";

const t = strings({
  zh: { cancel: "取消", close: "关闭", confirm: "确认" },
  en: { cancel: "Cancel", close: "Close", confirm: "Confirm" },
});
// `body` items: a paragraph string, or { label, items: [literal values] }.
function content(body) {
  const root = h("div.a-dialog-body");
  for (const item of Array.isArray(body) ? body : [body]) {
    if (item === null || item === undefined || item === "") continue;
    if (typeof item === "string") root.append(h("p", item));
    else if (item instanceof Node) root.append(item);
    else
      root.append(
        item.label ? h("p.a-dialog-label", item.label) : null,
        h(
          "ul.a-dialog-list",
          item.items.map((value) => h("li", value)),
        ),
      );
  }
  return root;
}
export function isDialogOpen() {
  return Boolean(document.querySelector("dialog.a-dialog[open]"));
}
export function confirm({
  title,
  body = [],
  confirmLabel = t("confirm"),
  cancelLabel = t("cancel"),
  tone = "default",
}) {
  if (typeof window.HTMLDialogElement !== "function")
    return Promise.resolve(window.confirm(title));
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const cancel = h("button.a-button", { type: "button" }, cancelLabel);
    const accept = h(
      "button.a-button",
      {
        type: "button",
        class: tone === "danger" ? "is-danger" : "is-primary",
      },
      confirmLabel,
    );
    const heading = h("h2.a-dialog-title", { id: `dlg-${Date.now()}` }, title);
    const dialog = h(
      "dialog.a-dialog",
      { "aria-labelledby": heading.id, dataset: { tone } },
      h(
        "div.a-dialog-head",
        h("span.a-dialog-icon", icon(tone === "danger" ? "alert" : "info")),
        heading,
      ),
      content(body),
      h("div.a-dialog-actions", cancel, accept),
    );
    let accepted = false;
    cancel.addEventListener("click", () => dialog.close());
    accept.addEventListener("click", () => {
      accepted = true;
      dialog.close();
    });
    dialog.addEventListener(
      "close",
      () => {
        dialog.remove();
        if (previous?.isConnected) previous.focus({ preventScroll: true });
        resolve(accepted);
      },
      { once: true },
    );
    document.body.append(dialog);
    dialog.showModal();
    (tone === "danger" ? cancel : accept).focus();
  });
}
// Side sheet for details and secondary forms. Returns { node, body, close }.
export function sheet({ title, subtitle, onClose, wide = false, beforeClose }) {
  const previous = document.activeElement;
  const body = h("div.a-sheet-body");
  const heading = h("h2.a-sheet-title", { id: `sheet-${Date.now()}` }, title);
  const closeButton = h(
    "button.a-icon-button",
    { type: "button", "aria-label": t("close") },
    icon("close"),
  );
  const dialog = h(
    "dialog.a-sheet",
    { "aria-labelledby": heading.id, class: wide ? "is-wide" : null },
    h(
      "header.a-sheet-head",
      h("div", heading, subtitle ? h("p.a-sheet-subtitle", subtitle) : null),
      closeButton,
    ),
    body,
  );
  function close() {
    if (dialog.open) dialog.close();
  }
  // `beforeClose` may veto a user-initiated close (e.g. unsaved drafts); close() never asks.
  const request = () => {
    if (!beforeClose || beforeClose() !== false) close();
  };
  closeButton.addEventListener("click", request);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) request();
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    request();
  });
  dialog.addEventListener(
    "close",
    () => {
      dialog.remove();
      onClose?.();
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    },
    { once: true },
  );
  document.body.append(dialog);
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  closeButton.focus();
  return { node: dialog, body, close };
}
