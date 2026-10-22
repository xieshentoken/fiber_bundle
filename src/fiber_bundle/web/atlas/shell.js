// Application frame: sidebar navigation, top bar and page header. No data reads.
import { $, h, icon, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  groups,
  icons,
  navText,
  pageSummary,
  pageTitle,
  routeHash,
} from "./nav.js";
import { relative } from "./format.js";

const t = strings({
  zh: {
    search: "搜索或跳转…",
    navigation: "主导航",
    openMenu: "打开导航",
    closeMenu: "关闭导航",
    collapse: "收起侧栏",
    expand: "展开侧栏",
    refresh: "刷新",
    updated: "更新于 {time}",
    connected: "已连接本机后台",
    disconnected: "无法连接本机后台",
    connecting: "正在连接…",
    checking: "正在检查连接…",
    signOut: "退出登录",
    theme: "切换深色 / 浅色",
    chooseTheme: "Fiber bundle，切换主题",
    language: "English",
    classic: "切换到经典界面",
    account: "管理员",
    local: "本机管理",
  },
  en: {
    search: "Search or jump to…",
    navigation: "Main navigation",
    openMenu: "Open navigation",
    closeMenu: "Close navigation",
    collapse: "Collapse sidebar",
    expand: "Expand sidebar",
    refresh: "Refresh",
    updated: "Updated {time}",
    connected: "Connected to local backend",
    disconnected: "Can't reach local backend",
    connecting: "Connecting…",
    checking: "Checking connection…",
    signOut: "Sign out",
    theme: "Toggle dark / light",
    chooseTheme: "Fiber bundle, choose theme",
    language: "中文",
    classic: "Switch to Classic UI",
    account: "Administrator",
    local: "Local admin",
  },
});

// Same source asset and crop as the classic application brand.
function applicationMark() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  for (const [key, value] of Object.entries({
    viewBox: "260 250 740 520",
    width: "28",
    height: "28",
    "aria-hidden": "true",
    focusable: "false",
  }))
    svg.setAttribute(key, value);
  const image = document.createElementNS(ns, "image");
  image.setAttribute("href", "/static/logo-original.png");
  image.setAttribute("width", "1254");
  image.setAttribute("height", "1254");
  svg.append(image);
  return svg;
}

export function createShell({
  onNavigate,
  onPalette,
  onSignOut,
  onToggleMode,
  onToggleLang,
  onClassic,
  onChooseTheme,
}) {
  const app = $("#atlas-app");
  const nav = h("nav.a-nav", { "aria-label": t("navigation") });
  const links = new Map(),
    badges = new Map();
  for (const group of groups) {
    const list = h("ul");
    for (const page of group.pages) {
      const badge = h("b.a-nav-badge", { hidden: true });
      const link = h(
        "a.a-nav-link",
        { href: routeHash(page), dataset: { page } },
        icon(icons[page]),
        h("span.a-nav-label", pageTitle(page)),
        badge,
      );
      link.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        onNavigate(page);
      });
      link.dataset.tab = page;
      links.set(page, link);
      badges.set(page, badge);
      list.append(h("li", link));
    }
    nav.append(
      h(
        "div.a-nav-group",
        group.id ? h("p.a-nav-heading", navText("group_" + group.id)) : null,
        list,
      ),
    );
  }
  const status = h(
    "span.a-conn",
    { role: "status", dataset: { state: "connecting" } },
    h("i"),
    h("span", t("connecting")),
  );
  const searchButton = h(
    "button.a-search-trigger",
    {
      type: "button",
      "aria-haspopup": "dialog",
      "aria-keyshortcuts": "Meta+K Control+K",
      onclick: onPalette,
    },
    icon("search"),
    h("span", t("search")),
    h("kbd", /Mac/.test(navigator.platform) ? "⌘K" : "Ctrl K"),
  );
  const sidebar = h(
    "aside.a-sidebar",
    { id: "a-sidebar" },
    h(
      "button.a-brand",
      {
        id: "theme-toggle",
        type: "button",
        title: t("chooseTheme"),
        "aria-label": t("chooseTheme"),
        "aria-haspopup": "dialog",
        "aria-controls": "theme-picker",
        "aria-expanded": "false",
        onclick: onChooseTheme,
      },
      applicationMark(),
      h("div", h("strong", "Fiber bundle"), h("span", t("local"))),
    ),
    searchButton,
    nav,
    h(
      "div.a-sidebar-foot",
      status,
      h(
        "div.a-foot-actions",
        h(
          "button.a-icon-button",
          {
            type: "button",
            title: t("theme"),
            "aria-label": t("theme"),
            onclick: onToggleMode,
          },
          icon("moon"),
        ),
        h(
          "button.a-icon-button",
          {
            type: "button",
            title: t("language"),
            "aria-label": t("language"),
            onclick: onToggleLang,
          },
          icon("globe"),
        ),
        h(
          "button.a-icon-button",
          {
            type: "button",
            title: t("classic"),
            "aria-label": t("classic"),
            onclick: onClassic,
          },
          icon("swap"),
        ),
        h(
          "button.a-icon-button",
          {
            type: "button",
            title: t("signOut"),
            "aria-label": t("signOut"),
            onclick: onSignOut,
          },
          icon("logout"),
        ),
      ),
    ),
  );
  const scrim = h("div.a-scrim", {
    hidden: true,
    onclick: () => setMenu(false),
  });
  const menuButton = h(
    "button.a-icon-button.a-menu-button",
    {
      type: "button",
      "aria-label": t("openMenu"),
      "aria-controls": "a-sidebar",
      "aria-expanded": "false",
      onclick: () => setMenu(!app.classList.contains("is-menu-open")),
    },
    icon("menu"),
  );
  const crumb = h("div.a-crumb");
  const topbar = h(
    "header.a-topbar",
    menuButton,
    crumb,
    h(
      "button.a-search-trigger.is-compact",
      { type: "button", "aria-label": t("search"), onclick: onPalette },
      icon("search"),
    ),
  );
  const warning = h("div.a-banner", {
    id: "a-session-warning",
    role: "status",
    hidden: true,
  });
  const view = h("main.a-view", { id: "atlas-view", tabindex: "-1" });
  clear(app, sidebar, scrim, h("div.a-main", topbar, warning, view));

  function setMenu(open) {
    app.classList.toggle("is-menu-open", open);
    scrim.hidden = !open;
    menuButton.setAttribute("aria-expanded", String(open));
    menuButton.setAttribute(
      "aria-label",
      open ? t("closeMenu") : t("openMenu"),
    );
  }
  return {
    view,
    warning,
    setActive(page) {
      for (const [id, link] of links)
        link.toggleAttribute("aria-current", id === page);
      if (links.get(page)) links.get(page).setAttribute("aria-current", "page");
      clear(crumb, h("span", pageTitle(page)));
      setMenu(false);
    },
    setBadge(page, count, title = "") {
      const badge = badges.get(page);
      if (!badge) return;
      badge.hidden = !count;
      badge.textContent = count > 99 ? "99+" : String(count || "");
      badge.title = title;
    },
    setConnection(state) {
      status.dataset.state = state;
      status.lastElementChild.textContent = t(state);
    },
  };
}

// Standard page header: title, purpose/limits summary, actions and refresh stamp.
export function pageHeader(
  page,
  { title, summary, actions = [], onRefresh, back } = {},
) {
  const stamp = h("span.a-stamp", { "aria-live": "off" });
  let stampedAt = 0;
  const refresh = onRefresh
    ? h(
        "button.a-button.is-ghost",
        { type: "button", title: t("refresh"), onclick: onRefresh },
        icon("refresh"),
        h("span", t("refresh")),
      )
    : null;
  const node = h(
    "header.a-page-head",
    h(
      "div.a-page-titles",
      back
        ? h("a.a-back", { href: back.href }, icon("back"), back.label)
        : null,
      h("h1", title || pageTitle(page)),
      h("p.a-page-summary", summary ?? pageSummary(page)),
    ),
    h("div.a-page-actions", ...actions, stamp, refresh),
  );
  const timer = setInterval(() => {
    if (stampedAt)
      stamp.textContent = t("updated", { time: relative(stampedAt) });
  }, 30000);
  return {
    node,
    refreshButton: refresh,
    stamp() {
      stampedAt = Date.now();
      stamp.textContent = t("updated", { time: relative(stampedAt) });
    },
    dispose() {
      clearInterval(timer);
    },
  };
}
