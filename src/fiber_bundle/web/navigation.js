import { setText, uiText } from "./i18n/index.js";
// Navigation presentation only. Feature loading and authorization stay in app/session.
import { $ } from "./ui.js";
import { icon } from "./icons.js";

export function decorateNavigation() {
  const tooltips = [];
  let focusedTooltip = null;
  function hide() {
    focusedTooltip = null;
    tooltips.forEach((tooltip) => {
      tooltip.hidden = true;
    });
  }
  function describe(button, id, label, available = () => true) {
    const tooltip = document.createElement("div");
    tooltip.className = "sidebar-tooltip";
    tooltip.id = id;
    tooltip.setAttribute("role", "tooltip");
    setText(tooltip, uiText(label()));
    tooltip.hidden = true;
    tooltips.push(tooltip);
    document.body.append(tooltip);
    button.setAttribute("aria-describedby", id);
    function show() {
      hide();
      if (!available()) return;
      setText(tooltip, uiText(label()));
      const box = button.getBoundingClientRect();
      tooltip.style.left = `${button.closest(".sidebar") ? box.right + 12 : box.left}px`;
      tooltip.style.top = `${button.closest(".sidebar") ? box.top : box.bottom + 8}px`;
      tooltip.hidden = false;
      if (document.activeElement === button) focusedTooltip = { button, show };
      const bounds = tooltip.getBoundingClientRect();
      tooltip.style.left = `${Math.max(8, Math.min(bounds.left, window.innerWidth - bounds.width - 8))}px`;
      tooltip.style.top = `${Math.max(8, Math.min(bounds.top, window.innerHeight - bounds.height - 8))}px`;
    }
    button.addEventListener("pointerenter", show);
    button.addEventListener("focus", show);
    for (const name of ["pointerleave", "blur", "click"])
      button.addEventListener(name, hide);
    return tooltip;
  }
  for (const button of document.querySelectorAll("[data-tab]")) {
    const label = button.textContent.trim();
    button.dataset.label = label;
    const text = document.createElement("span");
    text.className = "nav-label";
    setText(text, uiText(label));
    button.replaceChildren(icon(button.dataset.tab), text);
    setText(button, uiText(label), "aria-label");
    describe(
      button,
      `sidebar-tooltip-${button.dataset.tab}`,
      () => label,
      () => document.body.dataset.sidebar === "rail",
    );
  }
  const toggle = document.getElementById("sidebar-toggle");
  const brand = document.getElementById("theme-toggle");
  describe(brand, "theme-toggle-tooltip", () =>
    brand.getAttribute("aria-label"),
  );
  const tooltip = describe(toggle, "sidebar-toggle-tooltip", () =>
    toggle.getAttribute("aria-label"),
  );
  new window.MutationObserver(() => {
    setText(tooltip, uiText(toggle.getAttribute("aria-label")));
  }).observe(toggle, { attributes: true, attributeFilter: ["aria-label"] });
  window.addEventListener("resize", hide);
  document.addEventListener(
    "scroll",
    () => {
      if (focusedTooltip?.button === document.activeElement)
        focusedTooltip.show();
      else hide();
    },
    true,
  );
}
export function selectPage(button) {
  for (const other of document.querySelectorAll("[data-tab]")) {
    const active = other === button;
    if (active) other.setAttribute("aria-current", "page");
    else other.removeAttribute("aria-current");
    other.dataset.parentCurrent = String(
      other.dataset.tab === "worklist" &&
        ["pending", "batches", "documents", "media"].includes(
          button.dataset.tab,
        ),
    );
    $(other.dataset.tab + "-panel").hidden = !active;
  }
  setText($("page-name"), uiText(button.dataset.label));
  // Only scroll the bounded navigation container, never the page or focused field.
  if (button.hidden) return;
  const nav = button.parentElement;
  const bounds = nav.getBoundingClientRect();
  const item = button.getBoundingClientRect();
  if (nav.dataset?.orientation === "vertical") {
    const container = nav.closest(".sidebar");
    const frame = container.getBoundingClientRect();
    const search = container.querySelector("#command-open");
    const top = Math.max(
      frame.top + 8,
      search?.getBoundingClientRect().bottom + 8 || 0,
    );
    if (item.top < top) container.scrollTop += item.top - top;
    else if (item.bottom > frame.bottom - 8)
      container.scrollTop += item.bottom - frame.bottom + 8;
  } else if (item.left < bounds.left + 8)
    nav.scrollLeft += item.left - bounds.left - 8;
  else if (item.right > bounds.right - 8)
    nav.scrollLeft += item.right - bounds.right + 8;
}
