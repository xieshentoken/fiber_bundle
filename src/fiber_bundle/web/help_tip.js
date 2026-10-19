import { setText, uiText } from "./i18n/index.js";
// Supplementary explanations only. Required decisions stay beside their controls.
import { el } from "./ui.js";
let nextId = 0;
export function helpTip(label, explanation) {
  const host = el("span", null, "help-tip"),
    button = el("button", uiText("?"), "help-tip-trigger"),
    content = el("span", uiText(explanation), "help-tip-content");
  button.type = "button";
  setText(button, uiText(label), "aria-label");
  button.setAttribute("aria-expanded", "false");
  content.id = `help-tip-${++nextId}`;
  content.setAttribute("role", "tooltip");
  content.hidden = true;
  button.setAttribute("aria-describedby", content.id);
  let pinned = false,
    dismissed = false;
  function show() {
    if (dismissed) return;
    content.hidden = false;
    button.setAttribute("aria-expanded", "true");
  }
  function hide() {
    content.hidden = true;
    button.setAttribute("aria-expanded", "false");
  }
  host.addEventListener("pointerenter", show);
  host.addEventListener("pointerleave", () => {
    dismissed = false;
    if (!pinned && !host.contains(document.activeElement)) hide();
  });
  button.addEventListener("focus", show);
  host.addEventListener("focusout", (event) => {
    dismissed = false;
    if (!pinned && !host.contains(event.relatedTarget)) hide();
  });
  button.addEventListener("click", () => {
    pinned = !pinned;
    dismissed = false;
    if (pinned) show();
    else hide();
  });
  host.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || content.hidden) return;
    event.preventDefault();
    event.stopPropagation();
    pinned = false;
    dismissed = true;
    hide();
  });
  host.append(button, content);
  return host;
}
