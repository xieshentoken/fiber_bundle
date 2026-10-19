// Two local views retain the same forms and their unsaved values.
export function mountModelTabs(root) {
  const buttons = [...root.querySelectorAll('[role="tab"]')];
  function select(button, focus = false) {
    for (const tab of buttons) {
      const active = tab === button;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden =
        !active;
    }
    if (focus) button.focus();
  }
  buttons.forEach((button, index) => {
    button.addEventListener("click", () => select(button));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
        return;
      event.preventDefault();
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? buttons.length - 1
            : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) %
              buttons.length;
      select(buttons[next], true);
    });
  });
  select(buttons[0]);
}
