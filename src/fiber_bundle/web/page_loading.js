// Visible page loading owns its delay and reserved space; it never replaces form content.
export function createPageLoading(
  indicator,
  {
    schedule = setTimeout,
    cancel = clearTimeout,
    viewportHeight = () => window.innerHeight,
  } = {},
) {
  let current = null;
  const heights = new WeakMap();
  function clear() {
    if (!current) return;
    cancel(current.timer);
    for (const placeholder of current.placeholders) placeholder.remove();
    current.panel.style.minHeight = current.minHeight;
    current.panel.removeAttribute("aria-busy");
    indicator.hidden = true;
    current = null;
  }
  return {
    begin(panel) {
      clear();
      const bounds = panel.getBoundingClientRect();
      const available = Math.max(
        240,
        viewportHeight() - Math.max(0, bounds.top) - 24,
      );
      const previous = heights.get(panel);
      const reserved = Math.max(
        bounds.height,
        Math.min(previous || available, available),
      );
      const load = {
        panel,
        minHeight: panel.style.minHeight,
        timer: null,
        placeholders: [],
      };
      current = load;
      panel.style.minHeight = Math.ceil(reserved) + "px";
      panel.setAttribute("aria-busy", "true");
      indicator.hidden = true;
      load.timer = schedule(() => {
        if (current !== load) return;
        indicator.hidden = false;
        const empty = panel.querySelector?.(".rows:empty, .batch-list:empty");
        if (empty) {
          for (let index = 0; index < 2; index++) {
            const placeholder = document.createElement("div");
            placeholder.className = "skeleton";
            placeholder.setAttribute("aria-hidden", "true");
            load.placeholders.push(placeholder);
            empty.append(placeholder);
          }
        }
      }, 150);
      return () => {
        if (current !== load) return;
        heights.set(panel, panel.getBoundingClientRect().height);
        clear();
      };
    },
    clear,
  };
}
