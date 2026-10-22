import { navigateTheme, themeRenderer } from "./theme_entry.js";
import { createVaultRelocationView } from "./vault_relocation.js";
import { createUpdateNotice } from "./ui_updates.js";
import { bindStatic, phrase, setText, uiText } from "./i18n/index.js";
import { createLanguageControls } from "./i18n/controls.js";
import {
  mountWorkSections,
  mountManagementSections,
} from "./workspace_sections.js";
import { revealSection } from "./view_sections.js";
import { locateTask } from "./page_context.js";
import { createPageHeader } from "./page_header.js";
import { createCommandPalette } from "./command_palette.js";
import { createHomeView } from "./home.js";
import { createImportsView } from "./source_imports.js";
// Composition only. HTTP, session state and individual views live in separate modules.
import { rememberDraft, createDraftGuard } from "./unsaved_changes.js";
import { createPageLoading } from "./page_loading.js";
import { createAdminApi } from "./api.js";
import { createSession } from "./session.js";
import { createThemePicker } from "./theme_picker.js";
import { createAppearance } from "./appearance.js";
import { createSettingsView } from "./settings.js";
import { createSessionExpiry } from "./session_expiry.js";
import {
  $,
  action,
  enterList,
  finishLoading,
  message,
  setBusy,
  showLoading,
  setFeedbackPage,
  captureMessage,
} from "./ui.js";
import { createChannelsView } from "./channels.js";
import { renderVaults } from "./vaults.js";
import { createVaultCatalogView } from "./vault_catalog.js";
import { createVaultPlacementView } from "./vault_placements.js";
import { createVaultSaveView } from "./vault_save_settings.js";
import { createVaultScope } from "./vault_scope.js";
import { renderPending } from "./approvals.js";
import { createAgentConnectionsView } from "./agent_connections.js";
import { createClientsView } from "./clients.js";
import { createPendingBatchesView } from "./batches.js";

import { createMediaView } from "./media.js";
import { createDocumentsView } from "./documents.js";

import { createModelsView } from "./models.js";
import { createKnowledgeGraphView } from "./knowledge_graph.js";
import { createInventoryView } from "./management.js";
import { createExecutionView } from "./execution.js";
import { createSupplierView } from "./suppliers_view.js";
import { decorateNavigation, selectPage } from "./navigation.js";
import { createSidebar } from "./sidebar.js";
import { enhanceSelects } from "./select_controls.js";
import { createCreationView } from "./creation.js";
import { createBackendView } from "./backend.js";
import { createBackupsView } from "./backups.js";

bindStatic();
createLanguageControls();
decorateNavigation();
const pageHeader = createPageHeader();
const appearance = createAppearance();
if (window.fiberEntryTheme) appearance.selectTheme(window.fiberEntryTheme);
const sidebar = createSidebar();
const themePicker = createThemePicker(appearance, {
  beforeSelected: (theme) => {
    if (themeRenderer(theme.id) === "classic") return true;
    if (!draftGuard.confirmLeave()) return false;
    draftGuard.clear();
    return true;
  },
  onRendererSelected: (theme) => navigateTheme(theme.id),
  onSelected: (theme) =>
    message(phrase("已切换为{p0}。", { p0: uiText(theme.name) })),
});
$("theme-toggle").addEventListener("click", (event) =>
  themePicker.open(event.currentTarget),
);
window.addEventListener("pagehide", (event) => {
  themePicker.close();
  // A cached page is resumed with its existing controllers on browser Back.
  if (!event.persisted) {
    themePicker.destroy();
    appearance.destroy();
  }
});
enhanceSelects();
setFeedbackPage("home");
let homeView;
const session = createSession();
const commands = createCommandPalette(() => !!session.csrf);
const expiry = createSessionExpiry(
  session,
  $("workspace"),
  $("session-warning"),
);
const api = createAdminApi(session, {
  onMutation: () => homeView?.pause(),
  onConnection: (connected) => {
    setText(
      $("connection"),
      connected ? uiText("管理接口可达") : uiText("管理接口连接失败"),
    );
    $("connection").dataset.status = connected ? "connected" : "disconnected";
  },
  onUnauthorized: () => {
    const hadSession = !!session.csrf;
    signedOut(hadSession);
    message(
      hadSession
        ? "会话已失效，请在当前页面重新登录；刷新会清除暂存的文本草稿。"
        : "请登录以管理知识库和客户端。",
      hadSession,
    );
  },
});
const modelsView = createModelsView(api);
const documentsView = createDocumentsView(api);
const mediaView = createMediaView(api);
const clientsView = createClientsView(api, refreshData);
const batchesView = createPendingBatchesView(api, refreshBatches);
const vaultRelocationView = createVaultRelocationView(
  api.vaultRelocations,
  $("vault-relocation-panel"),
  refreshData,
);
const vaultSaveView = createVaultSaveView(api, $("vault-save-panel"));
const vaultPlacementView = createVaultPlacementView(
  api.vaultPlacements,
  $("vault-placement-panel"),
);
const vaultScope = createVaultScope({
  save: vaultSaveView,
  layout: vaultPlacementView,
});
homeView = createHomeView(api, session);
const managementViews = {
  home: homeView,
  worklist: { refresh: () => homeView.refresh(), clear() {} },
  imports: createImportsView(api, () => managementViews.imports.refresh()),
  clients: createAgentConnectionsView(api),
  vaults: createVaultCatalogView(api),
  channels: createChannelsView(api),
  inventory: createInventoryView(api),
  suppliers: createSupplierView(api,$("suppliers-panel")),
  graph: createKnowledgeGraphView(api),
  execution: createExecutionView(api),
  settings: createSettingsView(api, appearance, session, themePicker),
  creation: createCreationView(api),
  backend: createBackendView(api),
  backups: createBackupsView(api),
};
let latestLoad = 0;
mountWorkSections();
mountManagementSections({ vaultScope });
let latestBatchLoad = 0;
let latestPageLoad = 0;
const pageLoading = createPageLoading($("page-loading"));
const draftGuard = createDraftGuard($("workspace"), {
  extraChanges: (panel) =>
    (panel?.id === "models-panel" && modelsView.hasUnsavedChanges) ||
    (panel?.id === "settings-panel" &&
      managementViews.settings.hasUnsavedChanges),
  signedIn: () => !!session.csrf,
});

window.addEventListener("error", (event) => {
  console.error("页面错误", event.filename, event.lineno);
  message("页面发生错误，请刷新后重试。", true);
});
window.addEventListener("unhandledrejection", (event) => {
  console.error("未处理的异步错误", event.reason?.name || "unknown");
  message("操作未完成，请检查后台连接后重试。", true);
});

function signedOut(expired = false) {
  vaultScope.clear();
  themePicker.close();
  commands.close();
  document
    .querySelectorAll("dialog.confirm-dialog[open]")
    .forEach((dialog) => dialog.close());
  pageHeader.clear();
  homeView.setPage("");
  vaultSaveView.clear();
  vaultPlacementView.clear();
  vaultRelocationView.clear();
  if (expired) expiry.capture();
  $("session-warning").hidden = true;
  sidebar.setAuthenticated(false);
  setFeedbackPage(null);
  batchesView.clear();
  latestPageLoad++;
  pageLoading.clear();
  latestLoad++;
  latestBatchLoad++;
  session.clear();
  draftGuard.clear();
  Object.values(managementViews).forEach((view) => view.clear());
  clientsView.clear();
  documentsView.clear();
  mediaView.clear();
  modelsView.clear();
  $("workspace").hidden = true;
  $("logout").hidden = true;
  $("login-panel").hidden = false;
  $("vault-list").replaceChildren();
  $("pending-list").replaceChildren();
  $("batch-list").replaceChildren();
}
function signedIn(value) {
  message("");
  sidebar.setAuthenticated(true);
  session.accept(value);
  const page = document.querySelector('[data-tab][aria-current="page"]')
    ?.dataset.tab;
  setFeedbackPage(page);
  homeView.setPage(page);
  pageHeader.show(page || "home");
  batchesView.setVisible(page === "batches");
  $("login-panel").hidden = true;
  $("workspace").hidden = false;
  $("logout").hidden = false;
}
async function refreshData() {
  const active = document.querySelector('[data-tab][aria-current="page"]')
    ?.dataset.tab;
  if (["home", "worklist"].includes(active)) return homeView.refresh();
  const revision = session.revision,
    load = ++latestLoad,
    batchLoad = ++latestBatchLoad;
  const timer = setTimeout(() => {
    if (load !== latestLoad || revision !== session.revision) return;
    showLoading("vault-list");
    showLoading("pending-list");
    showLoading("batch-list");
  }, 150);
  try {
    const batchesRequest = api.pendingBatches().then(
      (value) => ({ value }),
      (error) => ({ error }),
    );
    const [vaults, clients] = await Promise.all([api.vaults(), api.clients()]);
    if (load !== latestLoad || revision !== session.revision) return;
    vaultScope.setData(vaults, clients);
    renderVaults(
      vaults,
      api,
      refreshData,
      managementViews.vaults.prefill,
      vaultSaveView.open,
      (vault) => vaultPlacementView.open(vault, clients),
      (vault) => navigate("clients", null, { connectionVaultId: vault.id }),
      vaultRelocationView.open,
    );
    clientsView.render(
      vaults,
      clients.filter((client) => !client.managed_connection_id),
    );
    renderPending(vaults, clients, api, refreshData);
    const batches = await batchesRequest;
    if (load !== latestLoad || revision !== session.revision) return;
    if (batchLoad === latestBatchLoad) {
      if (batches.error) {
        setText($("batch-list"), uiText("批次加载失败，请稍后重试。"));
      } else {
        batchesView.render(batches.value);
      }
    }
    const activeTab = document.querySelector('[data-tab][aria-current="page"]')
      ?.dataset.tab;
    if (managementViews[activeTab]) await managementViews[activeTab].refresh();
    rememberDraft($("workspace"));
  } finally {
    clearTimeout(timer);
    if (load === latestLoad) {
      finishLoading("vault-list");
      finishLoading("pending-list");
      finishLoading("batch-list");
    }
  }
}
async function refreshBatches() {
  const revision = session.revision,
    load = ++latestBatchLoad;
  const batches = await api.pendingBatches();
  if (revision === session.revision && load === latestBatchLoad) {
    batchesView.render(batches);
    rememberDraft($("batches-panel"));
  }
}
const updateNotice = createUpdateNotice(
  document.querySelector("main") || document.body,
);
async function readHealth() {
  const health = await api.health();
  updateNotice.observe(health);
  homeView.setBuild(health);
  return health;
}
async function reconnect() {
  let notify = captureMessage("");
  const hadSession = !!session.csrf;
  clientsView.clearToken();
  setText($("connection"), uiText("正在连接管理接口"));
  $("connection").dataset.status = "connecting";
  await readHealth();
  try {
    signedIn(await api.session());
    notify = captureMessage("");
    await refreshData();
    expiry.restore();
    enterList(document.querySelector("#workspace > section:not([hidden])"));
    notify("");
  } catch (error) {
    notify(
      error.status === 401 && !hadSession
        ? "请登录以管理知识库和客户端。"
        : error.message,
      error.status !== 401 || hadSession,
    );
  }
}
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setBusy(button, true);
  try {
    signedIn(await api.login($("password").value));
    await refreshData();
    const recovered = expiry.restore();
    enterList(document.querySelector("#workspace > section:not([hidden])"));
    message(
      recovered
        ? "已重新登录，文本草稿已还原。请核对表单选项；文件与凭据需重新选择。"
        : "",
    );
  } catch (error) {
    message(error.message, true);
  } finally {
    $("password").value = "";
    setBusy(button, false);
    button.disabled = false;
  }
});
action($("refresh"), reconnect);
action($("logout"), async () => {
  if (!draftGuard.confirmLeave()) return;
  await api.logout();
  expiry.discard();
  signedOut();
  message("已退出登录。");
});
function navigate(page, engine, target = {}) {
  themePicker.close();
  const button = document.querySelector(`[data-tab="${page}"]`);
  if (!button) return;
  const currentTab = button.getAttribute("aria-current") === "page";
  // Clicking current tab does nothing, preventing accidental clear of unsaved changes
  if (currentTab && !target.taskId) {
    sidebar.navigated();
    return;
  }
  if (!draftGuard.confirmLeave()) return;
  const changed = true;
  clientsView.clearToken();
  vaultSaveView.clear();
  vaultPlacementView.clear();
  vaultRelocationView.clear();
  Object.entries(managementViews).forEach(([name, view]) => {
    if (!["home", "worklist"].includes(name)) view.clear();
  });
  commands.close();
  homeView.setPage(page);
  documentsView.clear();
  mediaView.clear();
  modelsView.clear();
  latestBatchLoad++;
  batchesView.setVisible(button.dataset.tab === "batches");
  setFeedbackPage(button.dataset.tab);
  selectPage(button);
  pageHeader.show(page);
  sidebar.navigated();
  const panel = $(button.dataset.tab + "-panel");
  const view =
    (["vaults", "pending", "clients"].includes(page)
      ? { refresh: refreshData }
      : managementViews[button.dataset.tab]) ||
    {
      models: modelsView,
      documents: documentsView,
      media: mediaView,
      batches: { refresh: refreshBatches },
    }[button.dataset.tab];
  const load = ++latestPageLoad;
  if (!view) {
    pageLoading.clear();
    enterList(panel);
    return;
  }
  const finish = pageLoading.begin(panel);
  view
    .refresh()
    .then(async () => {
      if (load === latestPageLoad && changed) {
        rememberDraft(panel);
        enterList(panel);
        if (page === "clients" && target.connectionVaultId)
          managementViews.clients.prefillVault(target.connectionVaultId);
        if (
          page === "media" &&
          target.taskKind === "videos" &&
          target.clientId &&
          target.vaultId
        )
          await mediaView.selectVideoScope(target.clientId, target.vaultId);
        if (page === "vaults" && target.exportVaultId) {
          const destination = panel.querySelector(
            `[data-export-vault="${target.exportVaultId}"]`,
          );
          if (destination) {
            destination.closest(".vault-actions").open = true;
            destination.open = true;
            destination.dataset.exportModule = target.exportModuleId || "";
            destination.querySelector("[data-export-open]").click();
            destination.scrollIntoView({ block: "start" });
          } else message("该知识库当前不可用，请核对接入状态与权限。", true);
        }
        if (page === "imports" && target.clientId && target.vaultId)
          await managementViews.imports.selectScope(
            target.clientId,
            target.vaultId,
          );
        if (
          page === "vaults" &&
          target.taskKind === "automatic" &&
          target.vaultId
        ) {
          const vault = (await api.vaults()).find(
            (v) => v.id === target.vaultId,
          );
          if (load !== latestPageLoad) return;
          if (vault) await vaultSaveView.openTask(vault);
        }
        if (load !== latestPageLoad) return;
        if (page === "execution" && target.showUsage === "true")
          $("workspace-execution-usage-tab").click();
        if (page === "inventory" && target.openSourcePolicy) {
          revealSection($("source-policy-settings"));
          $("source-policy-settings").open = true;
          $("source-policy-form").scrollIntoView({ block: "start" });
        }
        if (target.taskId && !locateTask(panel, target.taskKind, target.taskId))
          message(
            "该记录已不在当前列表或权限范围内，请核对来源与筛选条件。",
            true,
          );
        if (page === "imports" && engine)
          panel.querySelector(`[data-engine="${engine}"]`)?.click();
      }
    })
    .catch((error) => {
      if (load === latestPageLoad) message(error.message, true);
    })
    .finally(() => {
      finish();
    });
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab], [data-page-target]");
  if (button)
    navigate(
      button.dataset.tab || button.dataset.pageTarget,
      button.dataset.importEngine,
      button.dataset,
    );
});
setInterval(() => {
  if (expiry.update()) {
    signedOut(true);
    message(
      "会话已到期，请在当前页面重新登录；刷新或关闭会清除暂存的文本草稿。",
      true,
    );
  }
}, 1000);
setInterval(() => {
  if (!document.hidden) readHealth().catch(() => {});
  if (!document.hidden && session.csrf && !$("batches-panel").hidden) {
    const notify = captureMessage("batches");
    refreshBatches().catch((error) => notify(error.message, true));
  }
}, 10000);
window.addEventListener("pagehide", () => clientsView.clearToken());
reconnect().catch((error) => message(error.message, true));

action($("page-refresh"), async () => {
  if (!draftGuard.confirmLeave()) return;
  const page = document.querySelector('[data-tab][aria-current="page"]')
    ?.dataset.tab;
  if (page === "home" || page === "worklist") await homeView.refresh(true);
  else if (["vaults", "pending", "clients"].includes(page)) await refreshData();
  else if (page === "documents") await documentsView.refresh();
  else if (page === "media") await mediaView.refresh();
  else if (page === "models") await modelsView.refresh();
  else if (page === "batches") await refreshBatches();
  else await managementViews[page]?.refresh();
  pageHeader.stamp();
});
