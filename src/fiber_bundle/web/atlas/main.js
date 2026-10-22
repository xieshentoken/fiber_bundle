// Atlas composition root: session, routing, view lifecycle. HTTP goes only through the
// shared API client (../api.js); views never call fetch directly.
import { setFeedbackPage } from "../ui.js";
import { createAppearance } from "../appearance.js";
import { locale } from "../i18n/index.js";
import { createAdminApi } from "../api.js";
import { createSession } from "../session.js";
import { createSessionExpiry } from "../session_expiry.js";
import { createDraftGuard, rememberDraft } from "../unsaved_changes.js";
import { $, h } from "./dom.js";
import { setLang, strings } from "./i18n.js";
import { applyPrefs, readPrefs, switchUi, writePrefs } from "./prefs.js";
import { createShell, pageHeader } from "./shell.js";
import { createStore } from "./store.js";
import { parseRoute, routeHash, pageTitle } from "./nav.js";
import { toast, toastError } from "./feedback.js";
import { failure, loading } from "./ui.js";
import { createLogin } from "./login.js";
import { createPalette } from "./palette.js";
import { enhanceSelects } from "../select_controls.js";
import { createConnectionMonitor } from "./connection.js";
import { switchLocale } from "./locale_switch.js";
import { openThemePicker } from "./theme_choices.js";

applyPrefs(document.documentElement, readPrefs(window.localStorage));
const appearance = createAppearance();
appearance.selectTheme("atlas");
let prefs = {
  ...readPrefs(window.localStorage),
  lang: locale() === "en" ? "en" : "zh",
  density: appearance.current.density,
  motion: appearance.current.motion,
};
setLang(prefs.lang);
applyPrefs(document.documentElement, prefs);
window
  .matchMedia("(prefers-color-scheme: dark)")
  .addEventListener("change", () => {
    applyPrefs(document.documentElement, prefs);
    appearance.selectTheme("atlas");
  });

const t = strings({
  zh: {
    title: "Fiber bundle",
    leave: "本页有未保存的内容，离开可能会清除这些更改。确定离开？",
    reload: "本页有未保存的内容，刷新可能会清除这些更改。确定刷新？",
    expired: "会话已到期，请重新登录；未保存的文本草稿会在登录后还原。",
    lost: "会话已失效，请重新登录。",
    signedOut: "已退出登录。",
    restored: "已重新登录，文本草稿已还原。请核对选项；文件与凭据需重新选择。",
    expiring: "登录将在 {n} 分钟内到期，请及时保存当前内容。",
    pageError: "页面发生错误，请刷新后重试。",
    viewFailed: "无法加载此页面。",
  },
  en: {
    title: "Fiber bundle",
    leave: "This page has unsaved changes that may be lost. Leave anyway?",
    reload:
      "This page has unsaved changes that a refresh may clear. Refresh anyway?",
    expired:
      "Your session expired. Sign in again; unsaved text drafts are restored afterwards.",
    lost: "Your session is no longer valid. Please sign in again.",
    signedOut: "Signed out.",
    restored:
      "Signed in again and text drafts restored. Check selections; files and credentials must be chosen again.",
    expiring: "Your sign-in expires within {n} min. Save your work.",
    pageError: "Something went wrong on this page. Refresh and try again.",
    viewFailed: "This page couldn't be loaded.",
  },
});

const session = createSession();
let shell,
  login,
  palette,
  current = null,
  generation = 0;
const api = createAdminApi(session, {
  onMutation: () => store.invalidate(),
  // Page failures never override the independent health monitor.
  onConnection: () => {},
  onUnauthorized: () => {
    const had = !!session.csrf;
    signedOut(had);
    if (had) toast(t("lost"), { tone: "warning" });
  },
});
const store = createStore(api);

function savePrefs(next) {
  prefs = writePrefs(window.localStorage, next);
  applyPrefs(document.documentElement, prefs);
  appearance.save({ density: prefs.density, motion: prefs.motion });
}
const ctx = {
  api,
  session,
  store,
  get prefs() {
    return { ...prefs };
  },
  savePrefs,
  appearance,
  navigate,
  setBadge: (page, count, title) => shell.setBadge(page, count, title),
  confirmLeave: (question) => guard.confirmLeave(question || t("leave")),
  confirmReload: () => confirmReload(),
  reloadForPrefs,
};

shell = createShell({
  onNavigate: (page) => navigate(page),
  onPalette: () => palette.open(),
  onChooseTheme: (event) => openThemePicker(ctx, event.currentTarget),
  onSignOut: signOut,
  onToggleMode: () => {
    const dark = document.documentElement.dataset.mode === "dark";
    savePrefs({ ...prefs, mode: dark ? "light" : "dark" });
  },
  onToggleLang: () =>
    reloadForPrefs({ ...prefs, lang: prefs.lang === "zh" ? "en" : "zh" }),
  onClassic: () => {
    if (confirmReload()) switchUi("classic");
  },
});
const connection = createConnectionMonitor(
  (options) => api.health(options),
  (state) => shell.setConnection(state),
);
const selects = enhanceSelects(document.body, {
  dialogs: true,
  popupClass: "a-select-popup",
});
window.addEventListener("pagehide", () => {
  connection.stop();
  selects.destroy();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) connection.stop();
  else connection.start();
});
const workspace = shell.view;
const guard = createDraftGuard(workspace, {
  extraChanges: () => Boolean(current?.view.dirty?.()),
  signedIn: () => !!session.csrf,
});
const expiry = createSessionExpiry(session, workspace, shell.warning);
palette = createPalette(ctx);
login = createLogin({
  api,
  onSignedIn: async (value) => {
    signedIn(value);
    await show(parseRoute(window.location.hash), { restore: true });
  },
  onClassic: () => switchUi("classic"),
  onLanguage: () =>
    reloadForPrefs({ ...prefs, lang: prefs.lang === "zh" ? "en" : "zh" }),
});

// After an explicit "leave anyway", clear draft tracking so the reload doesn't ask twice.
function confirmReload() {
  if (!guard.confirmLeave(t("leave"))) return false;
  guard.clear();
  return true;
}
// Language changes re-render every module's strings; a reload is the simplest exact way.
function reloadForPrefs(next) {
  if (!confirmReload()) return;
  const previous = { ...prefs };
  try {
    switchLocale(
      next.lang,
      () => {
        savePrefs(next);
        window.sessionStorage.setItem("fiber.theme.navigation.v1", "atlas");
      },
      () => window.location.reload(),
    );
  } catch {
    prefs = previous;
    applyPrefs(document.documentElement, prefs);
    toast(
      locale() === "en"
        ? "Language change could not be saved. Try again."
        : "语言切换未完成，请稍后重试。",
      { tone: "error" },
    );
  }
}

function signedIn(value) {
  session.accept(value);
  login.hide();
  $("#atlas-app").hidden = false;
  palette.setEnabled(true);
}
function signedOut(expired = false) {
  palette.close();
  palette.setEnabled(false);
  if (expired) expiry.capture();
  shell.warning.hidden = true;
  setFeedbackPage(null);
  session.clear();
  store.clear();
  if (!expired) dispose();
  $("#atlas-app").hidden = true;
  login.show();
}
async function signOut() {
  if (!guard.confirmLeave(t("leave"))) return;
  try {
    await api.logout();
  } catch (error) {
    toastError(error);
    return;
  }
  expiry.discard();
  signedOut(false);
  toast(t("signedOut"), { tone: "info" });
}

function dispose() {
  if (!current) return;
  current.view.dispose?.();
  current.header.dispose();
  current = null;
  guard.clear();
  workspace.replaceChildren();
}

function navigate(page, sub = "", params = {}, { history = "push" } = {}) {
  const next = { page, sub, params };
  const hash = routeHash(page, sub, params);
  if (current && !guard.confirmLeave(t("leave"))) return false;
  if (current && current.route.page === page && current.view.update?.(next)) {
    current.route = next;
    record(hash, history);
    return true;
  }
  record(hash, history);
  void show(next);
  return true;
}
function record(hash, mode) {
  if (mode === "none" || window.location.hash === hash) return;
  if (mode === "replace") window.history.replaceState(null, "", hash);
  else window.history.pushState(null, "", hash);
}

const loaders = {
  home: () => import("./view_home.js"),
  ask: () => import("./view_ask.js"),
  worklist: () => import("./view_inbox.js"),
  vaults: () => import("./view_vaults.js"),
  suppliers: () => import("./view_suppliers.js"),
  imports: () => import("./view_imports.js"),
  inventory: () => import("./view_inventory.js"),
  graph: () => import("./view_graph.js"),
  creation: () => import("./view_creation.js"),
  clients: () => import("./view_clients.js"),
  channels: () => import("./view_channels.js"),
  models: () => import("./view_models.js"),
  execution: () => import("./view_execution.js"),
  backend: () => import("./view_backend.js"),
  backups: () => import("./view_backups.js"),
  settings: () => import("./view_settings.js"),
};

async function show(route, { restore = false } = {}) {
  const mine = ++generation;
  const sameView = current && current.route.page === route.page && restore;
  if (!sameView) dispose();
  setFeedbackPage(route.page);
  shell.setActive(route.page);
  document.title = `${pageTitle(route.page)} · ${t("title")}`;
  if (sameView) {
    // Re-login on the same page keeps the DOM so captured drafts can be restored.
    await refreshCurrent();
    if (expiry.restore()) toast(t("restored"), { tone: "info" });
    return;
  }
  const section = h("section.a-page", {
    id: "atlas-" + route.page,
    dataset: { page: route.page },
  });
  workspace.replaceChildren(section);
  section.append(loading());
  let module;
  try {
    module = await loaders[route.page]();
  } catch (error) {
    if (mine !== generation) return;
    section.replaceChildren(failure(error, () => show(route)));
    toast(t("viewFailed"), { tone: "error" });
    return;
  }
  if (mine !== generation) return;
  const view = module.createView(ctx, route);
  const header = pageHeader(route.page, {
    ...(view.header || {}),
    onRefresh: view.refresh
      ? () => {
          if (guard.confirmLeave(t("reload"))) void refreshCurrent();
        }
      : null,
  });
  current = { route, view, header };
  section.replaceChildren(header.node, view.node);
  workspace.focus({ preventScroll: true });
  window.scrollTo(0, 0);
  await refreshCurrent();
  if (mine === generation && restore && expiry.restore())
    toast(t("restored"), { tone: "info" });
}
async function refreshCurrent() {
  const active = current;
  if (!active?.view.refresh) {
    active?.header.stamp();
    return;
  }
  const button = active.header.refreshButton;
  button?.setAttribute("aria-busy", "true");
  try {
    if (["vaults", "graph"].includes(active.route.page))
      store.invalidate("vaults", "clients");
    await active.view.refresh();
    if (active === current) {
      active.header.stamp();
      rememberDraft(workspace);
    }
  } catch (error) {
    if (active === current) toastError(error);
  } finally {
    button?.removeAttribute("aria-busy");
  }
}

window.addEventListener("popstate", () => {
  const route = parseRoute(window.location.hash);
  if (!session.csrf) return;
  if (current && !guard.confirmLeave(t("leave"))) {
    window.history.pushState(
      null,
      "",
      routeHash(current.route.page, current.route.sub, current.route.params),
    );
    return;
  }
  if (
    current &&
    current.route.page === route.page &&
    current.view.update?.(route)
  ) {
    current.route = route;
    return;
  }
  void show(route);
});
window.addEventListener("error", (event) => {
  console.error("atlas error", event.filename, event.lineno);
  toast(t("pageError"), { tone: "error" });
});
window.addEventListener("unhandledrejection", (event) => {
  console.error("atlas rejection", event.reason?.name || "unknown");
  toastError(event.reason);
});
document.addEventListener("keydown", (event) => {
  if (
    (event.metaKey || event.ctrlKey) &&
    event.key.toLowerCase() === "k" &&
    session.csrf
  ) {
    event.preventDefault();
    palette.open();
  }
});
setInterval(() => {
  const remaining = session.expiresAt - Date.now() / 1000;
  shell.warning.hidden = !session.csrf || remaining > 300 || remaining <= 0;
  if (!shell.warning.hidden)
    shell.warning.textContent = t("expiring", {
      n: Math.max(1, Math.ceil(remaining / 60)),
    });
  if (session.csrf && session.expired()) {
    signedOut(true);
    toast(t("expired"), { tone: "warning" });
  }
}, 1000);

async function boot() {
  try {
    await connection.refresh();
  } catch {
    /* Connection state is shown by onConnection; sign-in still offers retry. */
  }
  try {
    signedIn(await api.session());
    $("#atlas-boot").remove();
    await show(parseRoute(window.location.hash));
  } catch (error) {
    $("#atlas-boot")?.remove();
    login.show(error.status === 401 ? "" : error);
  }
}
void boot().finally(() => {
  if (!document.hidden) connection.start();
});
