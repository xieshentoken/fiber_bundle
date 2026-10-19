// Record presentation keeps technical evidence available without hiding state or actions.
import { action, el } from "./ui.js";
import { uiText } from "./i18n/index.js";

export function control(label, work, { localize = true, ...options } = {}) {
  const button = el("button", localize ? uiText(label) : label);
  button.type = "button";
  action(button, work, options);
  return button;
}
export function showJSON(parent, value, label = "技术详情") {
  const details = el("details", null, "technical-details");
  details.append(
    el("summary", uiText(label)),
    el("pre", JSON.stringify(value, null, 2)),
  );
  parent.append(details);
}
export function recordHeading(title, state, tone = "info") {
  const heading = el("div", null, "record-heading");
  heading.append(
    el("h3", title),
    el("span", uiText(state), "status-badge " + tone),
  );
  return heading;
}

// Reuse stable record nodes; unrelated replacements must not blur a focused control.
export function reconcileChildren(parent, nodes) {
  const desired = new Set(nodes);
  for (const child of Array.from(parent.children))
    if (!desired.has(child)) child.remove();
  nodes.forEach((node, index) => {
    if (parent.children[index] !== node)
      parent.insertBefore(node, parent.children[index] || null);
  });
}
