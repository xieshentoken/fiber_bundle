import { setText, translate, uiText } from "./i18n/index.js";
// Confirmation before an operation with consequences. Discarding unsaved edits keeps the
// synchronous native guard in unsaved_changes.js; this dialog never submits anything itself.
// No imports: operation_feedback.js reads isConfirming() without a module cycle.
let sequence = 0;
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text) setText(node, uiText(text));
  if (className) node.className = className;
  return node;
}
// `body` items: a string paragraph, or { label, items } for a list of literal values.
function content(body) {
  const root = el("div", null, "confirm-dialog-body");
  for (const item of body) {
    if (typeof item === "string") {
      root.append(el("p", item));
      continue;
    }
    if (item.label) root.append(el("p", item.label, "confirm-dialog-label"));
    const list = el("ul", null, "confirm-dialog-list technical");
    for (const value of item.items) list.append(el("li", value));
    root.append(list);
  }
  return root;
}
function plain(title, body) {
  const lines = [title];
  for (const item of body)
    if (typeof item === "string") lines.push(item);
    else lines.push(...(item.label ? [item.label] : []), ...item.items);
  return lines.join("\n");
}
export function isConfirming() {
  return Boolean(
    globalThis.document?.querySelector?.("dialog.confirm-dialog[open]"),
  );
}
export function confirmAction({
  title,
  body = [],
  confirmLabel,
  cancelLabel = "取消",
  tone = "default",
}) {
  const items = Array.isArray(body) ? body : [body];
  // Runtimes without <dialog> (unit-test fakes) use the equivalent native prompt.
  if (typeof window.HTMLDialogElement !== "function")
    return Promise.resolve(window.confirm(translate(plain(title, items))));
  return new Promise((resolve) => {
    const id = `confirm-dialog-${++sequence}`,
      previous = document.activeElement,
      dialog = el("dialog", null, "confirm-dialog"),
      heading = el("h2", title),
      actions = el("div", null, "confirm-dialog-actions"),
      cancel = el("button", uiText(cancelLabel)),
      accept = el(
        "button",
        uiText(confirmLabel),
        tone === "danger" ? "danger-primary" : "primary",
      );
    let accepted = false;
    heading.id = id + "-title";
    dialog.setAttribute("aria-labelledby", heading.id);
    if (tone === "danger") dialog.dataset.tone = "danger";
    cancel.type = accept.type = "button";
    actions.append(cancel, accept);
    dialog.append(heading, content(items), actions);
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
    // Destructive actions start on the safe choice.
    (tone === "danger" ? cancel : accept).focus();
  });
}
