import { setText, uiText } from "./i18n/index.js";
// Layout state only. Navigation, drafts and HTTP remain owned by their existing modules.
export function createSidebar() {
  const sidebar = document.getElementById("sidebar");
  const toggle = document.getElementById("sidebar-toggle");
  const close = document.getElementById("sidebar-close");
  const backdrop = document.getElementById("sidebar-backdrop");
  const main = document.querySelector("main");
  const header = document.querySelector("header");
  const connection = header.querySelector(".connection"),
    footer = document.getElementById("sidebar-footer"),
    connectionSlot = document.createComment("connection");
  connection.before(connectionSlot);
  const mobile = window.matchMedia("(max-width: 767px)");
  const compact = window.matchMedia("(max-width: 1023px)");
  let preference = null;
  try {
    const saved = window.localStorage.getItem("fiber.sidebar");
    if (["expanded", "rail"].includes(saved)) preference = saved;
  } catch {
    // Unavailable browser storage must not prevent navigation.
  }
  let authenticated = false;
  let drawer = false;
  function measureHeader() {
    const height = Math.ceil(header.getBoundingClientRect().height);
    if (!height) return;
    document.body.style.setProperty("--toolbar-height", `${height}px`);
    document.documentElement.style.setProperty(
      "--measured-toolbar-height",
      `${height}px`,
    );
  }
  if (typeof ResizeObserver !== "undefined")
    new ResizeObserver(measureHeader).observe(header);

  function render() {
    if (authenticated && !mobile.matches) footer.append(connection);
    else connectionSlot.after(connection);
    const expanded =
      !mobile.matches &&
      (preference ?? (compact.matches ? "rail" : "expanded")) === "expanded";
    document.body.dataset.sidebar = mobile.matches
      ? "drawer"
      : expanded
        ? "expanded"
        : "rail";
    document.body.dataset.drawerOpen = String(drawer && authenticated);
    document.body.dataset.authenticated = String(authenticated);
    sidebar.hidden = !authenticated;
    sidebar.inert = mobile.matches && !drawer;
    toggle.hidden = !authenticated;
    close.hidden = !mobile.matches;
    backdrop.hidden = !authenticated || !mobile.matches || !drawer;
    toggle.setAttribute(
      "aria-expanded",
      String(mobile.matches ? drawer : expanded),
    );
    const label = (mobile.matches ? drawer : expanded)
      ? "收起侧栏"
      : "展开侧栏";
    setText(toggle, uiText(label), "aria-label");
    main.inert = header.inert = authenticated && mobile.matches && drawer;
    if (mobile.matches) {
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
    } else {
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
    }
  }
  function closeDrawer(restore = true) {
    if (!drawer) return;
    drawer = false;
    render();
    if (restore) toggle.focus({ preventScroll: true });
  }
  toggle.addEventListener("click", () => {
    if (mobile.matches) {
      drawer = !drawer;
      render();
      if (drawer)
        sidebar
          .querySelector('[aria-current="page"]')
          ?.focus({ preventScroll: true });
    } else {
      preference =
        document.body.dataset.sidebar === "expanded" ? "rail" : "expanded";
      try {
        window.localStorage.setItem("fiber.sidebar", preference);
      } catch {
        // The preference is still retained for this window.
      }
      render();
    }
  });
  close.addEventListener("click", () => closeDrawer());
  backdrop.addEventListener("click", () => closeDrawer());
  sidebar.addEventListener("keydown", (event) => {
    if (!drawer || !mobile.matches) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeDrawer();
    } else if (event.key === "Tab") {
      const controls = [
        ...sidebar.querySelectorAll("button:not(:disabled)"),
      ].filter((node) => !node.hidden);
      const first = controls[0],
        last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
  });
  function resized() {
    const hadDrawer = drawer;
    drawer = false;
    render();
    if (hadDrawer) toggle.focus({ preventScroll: true });
  }
  mobile.addEventListener("change", resized);
  compact.addEventListener("change", render);
  render();
  measureHeader();
  return {
    setAuthenticated(value) {
      authenticated = value;
      drawer = false;
      render();
    },
    navigated() {
      if (drawer) {
        closeDrawer(false);
        const heading = document.getElementById("page-name");
        heading.focus({ preventScroll: true });
      }
    },
  };
}
