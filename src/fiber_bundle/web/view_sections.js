import { setText, uiText } from "./i18n/index.js";
// Local presentation only. Moving a node preserves its values and event owners.
import { el } from "./ui.js";

export function createSections(host, entries, { id, selected, onSelect } = {}) {
  const nav = el("div", null, "view-tabs"),
    panels = new Map(),
    buttons = [];
  nav.setAttribute("role", "tablist");
  setText(nav, uiText("功能分区"), "aria-label");
  function select(key, focus = false) {
    if (!panels.has(key)) return;
    for (const button of buttons) {
      const active = button.dataset.section === key;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      panels.get(button.dataset.section).hidden = !active;
      if (active && focus) button.focus({ preventScroll: true });
    }
    host.dataset.selectedSection = key;
    onSelect?.(key);
  }
  for (const entry of entries) {
    const button = el("button", uiText(entry.label)),
      panel = el("div", null, "view-panel");
    button.type = "button";
    button.dataset.section = entry.id;
    button.id = `${id}-${entry.id}-tab`;
    button.dataset.focusKey = button.id;
    panel.id = `${id}-${entry.id}-view`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", panel.id);
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", button.id);
    panel.append(...entry.nodes.filter(Boolean));
    panels.set(entry.id, panel);
    buttons.push(button);
    nav.append(button);
    button.addEventListener("click", () => select(entry.id));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
        return;
      event.preventDefault();
      const index = buttons.indexOf(button),
        next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? buttons.length - 1
              : (index +
                  (event.key === "ArrowRight" ? 1 : -1) +
                  buttons.length) %
                buttons.length;
      select(buttons[next].dataset.section, true);
    });
  }
  host.append(nav, ...panels.values());
  select(panels.has(selected) ? selected : entries[0].id);
  return { select, panels };
}

export function disclosure(label, ...nodes) {
  const details = el("details", null, "view-disclosure");
  details.append(el("summary", uiText(label)), ...nodes.filter(Boolean));
  return details;
}

// Capture only view state; never persist confirmation or mutate feature drafts.
export function retainView(root, render) {
  const opened = new Set(
    [...root.querySelectorAll("details[data-view-key][open]")].map(
      (n) => n.dataset.viewKey,
    ),
  );
  const focused = root.contains(document.activeElement)
    ? document.activeElement
    : null;
  const key = focused?.dataset.focusKey;
  const x = window.scrollX,
    y = window.scrollY;
  render();
  for (const details of root.querySelectorAll("details[data-view-key]"))
    details.open = opened.has(details.dataset.viewKey);
  if (key)
    [...root.querySelectorAll("[data-focus-key]")]
      .find((n) => n.dataset.focusKey === key)
      ?.focus({ preventScroll: true });
  if (root.isConnected) window.scrollTo(x, y);
}

export function revealSection(target) {
  const panels = [];
  for (let node = target; node; node = node.parentElement) {
    if (node.matches?.("details")) node.open = true;
    if (node.matches?.('.view-panel[role="tabpanel"]')) panels.unshift(node);
  }
  for (const panel of panels) {
    const button = document.getElementById(
      panel.getAttribute("aria-labelledby"),
    );
    if (button?.getAttribute("aria-selected") !== "true") button?.click();
  }
}
