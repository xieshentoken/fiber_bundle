import { createGraphScopeSwitch } from "./graph_scope_switch.js";
import { confirmAction } from "./confirm_dialog.js";
import { createGraphScopeControls } from "./graph_scope_controls.js";
import { createGraphScopeReads } from "./graph_scope_read.js";
import {
  authorizedGraphScopes,
  sameGraphScope,
} from "./graph_scope_preferences.js";
import { coverageNotice } from "./organization_coverage.js";
import { phrase, setText, translate, uiText, msg } from "./i18n/index.js";
import { createOrganizationView } from "./organization.js";
// Scope-bound admissions and composition of selection, reviews, and task observation.
import { $, action, el, captureMessage } from "./ui.js";
import { control, showJSON } from "./record_ui.js";
import { hasUnsavedChanges } from "./unsaved_changes.js";
import { loadGraphScope } from "./knowledge_graph_loading.js";
import { createGraphSetup, graphLoadFailure } from "./knowledge_graph_setup.js";
import { createGraphMocView } from "./knowledge_graph_moc.js";
import {
  createNoteSelection,
  renderNoteSelection,
} from "./knowledge_graph_selection.js";
import { suggestionCard, candidatePage } from "./knowledge_graph_cards.js";
import { createGraphCanvas } from "./knowledge_graph_canvas.js";
import { createTaskObserver } from "./knowledge_graph_observer.js";
const jobStates = {
  queued: "待执行",
  running: "分析中",
  completed: "已完成",
  cancelled: "已取消",
  interrupted: "已中断",
  failed: "失败",
  stale: "过期",
};
export function createKnowledgeGraphView(api, { onOpenIndex } = {}) {
  const graph = api.graph,
    root = $("graph-content"),
    notesRoot = $("graph-notes"),
    jobsRoot = $("graph-jobs"),
    client = $("graph-client"),
    vault = $("graph-vault"),
    filter = $("graph-filter");
  let entered = false,
    submitting = 0;
  const defaultFilter = filter.value;
  let epoch = 0,
    clients = [],
    vaults = [],
    listing = { items: [] },
    page = 0,
    profile = null,
    selectedScope = null,
    previousFilter = filter.value,
    noteOffset = 0,
    noteQuery = "";
  async function write(work) {
    submitting++;
    try {
      return await work();
    } finally {
      submitting--;
    }
  }
  const pendingStarts = new Set();
  const observationTasks = (tasks) =>
    tasks.map((t) =>
      pendingStarts.has(t.id) && t.state === "queued"
        ? { ...t, state: "launch_requested" }
        : t,
    );
  const selection = createNoteSelection(),
    notify = () => captureMessage("graph"),
    scope = () => ({ client_id: client.value, vault_id: vault.value });
  const scopeControls = createGraphScopeControls(scope);
  const reads = createGraphScopeReads(scopeControls.readState);
  client.closest(".scope-bar").after(scopeControls.host);
  let organizationReadError = null;
  const organization = api.organization
    ? createOrganizationView(api.organization, scope, {
        onOpenIndex,
        onReadError: (error) => {
          organizationReadError = error;
        },
        onReadSuccess: () => {
          organizationReadError = null;
        },
      })
    : null;
  if (organization) $("graph-policy").before(organization.host);
  const moc = createGraphMocView(graph, scope, () => epoch);
  const setup = createGraphSetup(
    graph,
    scope,
    () => refresh(),
    () => profile,
  );
  $("graph-policy").after(setup.host);
  const noteSearch = el("input"),
    noteNavigation = el("div", null, "panel-actions"),
    notePage = el("span");
  setText(noteSearch, uiText("按标题或路径查找笔记"), "placeholder");
  setText(noteSearch, uiText("查找待分析笔记"), "aria-label");
  const previousNotes = control("上一页笔记", async () => {
    noteOffset = Math.max(0, noteOffset - 100);
    await loadScope();
  });
  const nextNotes = control("下一页笔记", async () => {
    noteOffset += 100;
    await loadScope();
  });
  noteNavigation.append(
    noteSearch,
    control("查找笔记", async () => {
      noteQuery = noteSearch.value.trim();
      noteOffset = 0;
      await loadScope();
    }),
    previousNotes,
    nextNotes,
    notePage,
  );
  notesRoot.before(noteNavigation);
  const canvas = createGraphCanvas(graph, scope);
  const status = el("p"),
    loadReady = control("加载已就绪的新结果", () => loadScope());
  status.setAttribute("role", "status");
  loadReady.hidden = true;
  jobsRoot.before(status, loadReady);
  const pendingReview = () =>
    !!root.querySelector('[data-review-pending="true"]');
  function dirty() {
    return (
      hasUnsavedChanges($("graph-panel")) ||
      moc.hasPending() ||
      pendingReview() ||
      organization?.dirty()
    );
  }
  function canReplace(target = $("graph-panel")) {
    if (
      pendingReview() ||
      submitting ||
      moc.busy?.() ||
      organization?.busy?.()
    ) {
      notify()(msg("graph_scope.busy"), true);
      return false;
    }
    return (
      !(
        hasUnsavedChanges(target) ||
        (target === $("graph-panel") &&
          (moc.hasPending() || organization?.dirty({ manual: false })))
      ) ||
      window.confirm(
        translate(
          "本页有未保存的内容或 MOC 预览，刷新或切换会清除这些更改。确定继续？",
        ),
      )
    );
  }
  function snapshot() {
    return JSON.stringify(
      [...$("graph-panel").querySelectorAll("input,textarea")]
        .filter((n) => n.dataset.draft !== "ignore")
        .map((n) => n.value),
    );
  }
  function anchor(id = null) {
    const cards = [...root.querySelectorAll("[data-candidate-id]")];
    const card = id
      ? cards.find((c) => c.dataset.candidateId === id)
      : document.activeElement?.closest("[data-candidate-id]") ||
        cards.find((c) => c.getBoundingClientRect().bottom > 0);
    return {
      id: id || card?.dataset.candidateId,
      top: card?.getBoundingClientRect().top,
      scroll: window.scrollY,
    };
  }
  function renderSuggestions(position = null) {
    const result = candidatePage(
      listing.items,
      filter.value,
      position?.id,
      page,
    );
    page = result.page;
    root.replaceChildren();
    const entities = new Map(
      listing.items
        .filter((i) => i.type === "entity" && i.value)
        .map((i) => [i.id, i.value.label]),
    );
    if (!result.filtered.length)
      root.append(
        el(
          "p",
          uiText(
            "当前没有可展示的建议。先明确选择笔记并执行分析；过期索引须重新核对。",
          ),
        ),
      );
    for (const item of result.filtered.slice(page * 20, page * 20 + 20))
      root.append(
        suggestionCard(item, {
          entities,
          review: graph.review,
          configuration: listing.configuration,
          scope: { ...selectedScope },
          epoch: () => epoch,
          moc,
          refresh: (id) => loadScope({ anchorId: id }),
        }),
      );
    if (result.filtered.length > 20)
      root.append(
        control("上一页", () => {
          if (canReplace(root)) {
            page = Math.max(0, page - 1);
            renderSuggestions();
          }
        }),
        el("span", phrase(" 第 {p0} 页 ", { p0: page + 1 })),
        control("下一页", () => {
          if (canReplace(root)) {
            page = Math.min(
              Math.ceil(result.filtered.length / 20) - 1,
              page + 1,
            );
            renderSuggestions();
          }
        }),
      );
    if (position?.id) {
      const card = [...root.children].find(
        (c) => c.dataset.candidateId === position.id,
      );
      if (result.missing)
        notify()(
          "原建议已移除、过期或不属于当前筛选，未定位到其他建议。",
          true,
        );
      else if (card)
        window.scrollBy(
          0,
          card.getBoundingClientRect().top - (position.top ?? 0),
        );
    } else if (position) window.scrollTo(0, position.scroll);
  }
  function selectVaults() {
    const prior = vault.value;
    const allowed = authorizedGraphScopes(clients, vaults).filter(
      (pair) => pair.client_id === client.value,
    );
    vault.replaceChildren();
    for (const v of vaults.filter((value) =>
      allowed.some((pair) => pair.vault_id === value.id),
    )) {
      const option = el("option", v.name);
      option.value = v.id;
      vault.append(option);
    }
    if ([...vault.options].some((o) => o.value === prior)) vault.value = prior;
  }
  function renderJobs(tasks, fixed, revision) {
    jobsRoot.replaceChildren();
    setText(
      status,
      phrase("当前结果代次：{p0}{p1}", {
        p0: listing.job_id || "尚无结果",
        p1: tasks.some((t) => t.state === "running")
          ? uiText("；正在观察分析任务（最多 3 分钟）")
          : uiText(""),
      }),
    );
    for (const job of tasks) {
      const card = el("article", null, "row record");
      card.append(
        el(
          "p",
          pendingStarts.has(job.id) && job.state === "queued"
            ? uiText("分析启动中")
            : uiText(jobStates[job.state] || job.state),
        ),
      );
      if (job.error_code) card.append(el("p", job.error_code));
      for (const [label, op] of job.state === "queued" &&
      pendingStarts.has(job.id)
        ? [["取消", "cancel"]]
        : job.state === "queued"
          ? [
              ["执行此任务", "start"],
              ["取消", "cancel"],
            ]
          : job.state === "running"
            ? [
                ["取消", "cancel"],
                ["核对中断", "quarantine"],
              ]
            : [])
        card.append(
          control(label, async () => {
            if (revision !== epoch || !canReplace()) return;
            const before = snapshot();
            const response = await write(() =>
              graph.jobAction(job.id, op, fixed),
            );
            if (
              op === "start" &&
              ["launch_requested", "running"].includes(response.state)
            )
              pendingStarts.add(job.id);
            if (revision !== epoch) return;
            if (before !== snapshot() && dirty()) {
              setText(
                status,
                uiText("任务状态已更新；当前草稿已保留，请手动刷新。"),
              );
              observer.start([
                { ...job, state: op === "start" ? "running" : job.state },
              ]);
              return;
            }
            await loadScope({ approved: true });
          }),
        );
      showJSON(card, job, "任务记录");
      jobsRoot.append(card);
    }
  }
  const observer = createTaskObserver({
    read: () => graph.jobs(selectedScope.client_id, selectedScope.vault_id),
    async onUpdate(tasks, watched) {
      for (const task of tasks)
        if (!["queued", "running"].includes(task.state))
          pendingStarts.delete(task.id);
      renderJobs(tasks, { ...selectedScope }, epoch);
      const finished = tasks.filter(
        (t) => watched.includes(t.id) && t.state === "completed",
      );
      const failed = tasks.filter(
        (t) =>
          watched.includes(t.id) &&
          ["failed", "cancelled", "interrupted", "stale"].includes(t.state),
      );
      if (finished.length && !finished.some((t) => t.id === listing.job_id)) {
        if (dirty()) {
          setText(
            status,
            uiText("新结果已就绪；审核/MOC 草稿已保留。确认后可加载。"),
          );
          loadReady.hidden = false;
        } else await loadScope({ automatic: true });
      } else if (failed.length)
        setText(
          status,
          phrase("任务已结束：{p0}；可手动刷新。", {
            p0: uiText(
              failed
                .map(
                  (t) =>
                    jobStates[t.state] +
                    (t.error_code ? " · " + t.error_code : ""),
                )
                .join("、"),
            ),
          }),
        );
    },
    onStop(reason, error) {
      if (reason === "timeout")
        setText(status, uiText("任务观察已超时，已停止轮询；可手动刷新。"));
      if (reason === "unavailable")
        setText(status, uiText("任务已不可用，已停止观察；请刷新核对权限。"));
      if (reason === "error") {
        if ([401, 403].includes(error?.status)) {
          clear();
          notify()("权限或会话失效，已清除当前范围。", true);
        } else
          setText(
            status,
            phrase("任务观察停止：{p0}；可手动刷新。", {
              p0: uiText(error.message),
            }),
          );
      }
    },
  });
  function scopeReadKey() {
    const fixed = scope(),
      changed = !sameGraphScope(selectedScope, fixed);
    return (
      "scope:" +
      JSON.stringify([
        fixed,
        changed ? "" : noteQuery,
        changed ? 0 : noteOffset,
      ])
    );
  }
  function loadScope(options = {}) {
    return reads.run(scopeReadKey(), () => readScope(options));
  }
  async function readScope({
    automatic = false,
    anchorId = null,
    approved = false,
  } = {}) {
    if (automatic && dirty()) {
      setText(status, uiText("新结果已就绪；草稿已保留。"));
      loadReady.hidden = false;
      return { skipped: true };
    }
    if (!automatic && !approved && !canReplace()) return { skipped: true };
    observer.stop();
    const before = snapshot(),
      position = anchor(anchorId),
      revision = ++epoch,
      fixed = scope();
    const same =
      selectedScope?.client_id === fixed.client_id &&
      selectedScope?.vault_id === fixed.vault_id;
    if (!same) {
      selection.clear();
      noteOffset = 0;
      noteQuery = "";
      noteSearch.value = "";
      filter.value = defaultFilter;
      previousFilter = defaultFilter;
      listing = { items: [] };
      canvas.clear();
      setup.stop();
      setup.reset();
      organization?.clear();
      setText(status, "");
      setText(notePage, "");
      $("graph-submit").disabled = true;
      $("graph-submit").dataset.policyDisabled = "true";
      previousNotes.disabled = nextNotes.disabled = true;
      loadReady.hidden = true;
      selectedScope = fixed;
      pendingStarts.clear();
      root.replaceChildren();
      notesRoot.replaceChildren();
      jobsRoot.replaceChildren();
      moc.clear();
      page = 0;
    }
    if (!fixed.client_id || !fixed.vault_id) {
      setup.stop();
      canvas.clear();
      organization?.clear();
      root.replaceChildren(el("p", msg("graph_scope.no_scope")));
      return { skipped: true };
    }
    scopeSwitch.remember(fixed);
    try {
      organizationReadError = null;
      const organizationRead = organization
        ? organization
            .refresh()
            .then(() => ({ error: organizationReadError }))
            .catch((error) => ({ error }))
        : Promise.resolve({});
      const { notes, coverage, suggestions, tasks, warnings } =
        await loadGraphScope(graph, fixed, noteQuery, noteOffset);
      if (revision !== epoch) return;
      if (pendingReview() || (before !== snapshot() && dirty())) {
        if (scopeSwitch.isPending() || automatic || !canReplace()) {
          setText(status, uiText("更新已返回；当前草稿已保留，请手动刷新。"));
          loadReady.hidden = false;
          observer.start(observationTasks(tasks));
          return;
        }
      }
      canvas.invalidate();
      selectedScope = fixed;
      listing = suggestions;
      moc.clear();
      loadReady.hidden = true;
      notify()("");
      renderNoteSelection(
        notesRoot,
        selection,
        notes,
        fixed.client_id + ":" + fixed.vault_id,
        profile?.enabled,
        $("graph-submit"),
      );
      const notice = coverageNotice(coverage, "graph-notes-coverage", false, {
        scope: fixed,
        api: api.organization,
      });
      if (notice) notesRoot.prepend(notice);
      setup.loaded(warnings);
      previousNotes.disabled = noteOffset === 0;
      nextNotes.disabled = notes.length < 100;
      setText(
        notePage,
        phrase("第 {p0} 页 · 本页 {p1} 篇；勾选仅作用于本页", {
          p0: Math.floor(noteOffset / 100) + 1,
          p1: notes.length,
        }),
      );
      if (!notes.length)
        notesRoot.append(
          el(
            "p",
            uiText("当前范围没有匹配的笔记，请调整搜索或返回上一页。"),
            "muted",
          ),
        );
      renderSuggestions(same ? position : null);
      renderJobs(tasks, fixed, revision);
      await moc.refresh(fixed, revision);
      if (revision === epoch) await canvas.refreshIfVisible();
      const organizationResult = await organizationRead;
      if (revision !== epoch) return;
      if ([401, 403].includes(organizationResult.error?.status))
        throw organizationResult.error;
      observer.start(observationTasks(tasks));
      if (organizationResult.error)
        return { partial: true, error: organizationResult.error };
    } catch (error) {
      if (revision !== epoch) return;
      {
        if ([401, 403].includes(error.status)) {
          clear({ keepRead: true });
          root.append(el("p", graphLoadFailure(error), "error"));
        } else {
          selection.clear();
          notesRoot.replaceChildren(el("p", graphLoadFailure(error), "error"));
          $("graph-submit").disabled = true;
          $("graph-submit").dataset.policyDisabled = "true";
          setup.fail(error);
        }
      }
      throw error;
    }
  }
  function refresh(options = {}) {
    if (!options.approved && !canReplace()) return Promise.resolve();
    return (
      reads.pending(scopeReadKey()) ||
      reads.run("catalog:" + JSON.stringify(scope()), () =>
        refreshCatalog({ ...options, approved: true }),
      )
    );
  }
  async function refreshCatalog({ approved = false } = {}) {
    if (!approved && !canReplace()) return;
    observer.stop();
    const before = snapshot(),
      revision = ++epoch;
    let newClients, newVaults, state;
    try {
      [newClients, newVaults, state] = await Promise.all([
        api.clients(),
        api.vaults(),
        graph.policy(),
      ]);
    } catch (error) {
      if (revision !== epoch) return;
      if ([401, 403].includes(error.status)) clear({ keepRead: true });
      throw error;
    }
    if (revision !== epoch) return;
    const confirmingScope = scopeSwitch.isPending();
    scopeSwitch.cancel();
    if (
      !confirmingScope &&
      (pendingReview() || (before !== snapshot() && dirty())) &&
      !canReplace()
    )
      return { skipped: true };
    clients = newClients.filter((c) => c.active);
    vaults = newVaults;
    profile = state;
    const choice = scopeControls.catalog(clients, vaults, scope(), !entered);
    entered = true;
    client.replaceChildren();
    for (const c of clients) {
      const option = el("option", c.name);
      option.value = c.id;
      client.append(option);
    }
    client.value = choice?.client_id || "";
    selectVaults();
    vault.value = choice?.vault_id || "";
    scopeControls.changed();
    setText(
      $("graph-policy"),
      state.configured
        ? uiText(
            (state.enabled ? "已启用显式本地分析" : "分析已关闭") +
              "；" +
              (state.value.engine.model?.name || "未配置语义模型"),
          )
        : uiText(
            "未配置运行环境。请按 README 构建固定本地镜像并保存配置；打开页面不会启动分析。",
          ),
    );
    $("graph-toggle").disabled = !state.configured;
    $("graph-toggle").dataset.policyDisabled = String(!state.configured);
    setText(
      $("graph-toggle"),
      state.enabled ? uiText("关闭分析") : uiText("启用分析"),
    );
    if (confirmingScope) {
      if (!sameGraphScope(choice, selectedScope)) {
        clear({ keepRead: true });
        vault.value = "";
        scopeControls.changed();
      }
      return { skipped: true };
    }
    await loadScope({ approved: true });
  }
  action($("graph-refresh"), refresh);
  action($("graph-toggle"), async () => {
    if (!profile?.value || !canReplace()) return;
    const before = snapshot(),
      fixed = epoch;
    await write(() =>
      graph.savePolicy({
        value: {
          ...profile.value,
          revision: profile.value.revision + 1,
          enabled: !profile.enabled,
        },
        expected_sha256: profile.configuration,
      }),
    );
    if (fixed !== epoch) return;
    if (before !== snapshot() && dirty()) {
      setText(status, uiText("配置已更新，草稿已保留；请手动刷新。"));
      return;
    }
    // Refresh does its own draft guard because the request may have overlapped edits.
    await refresh({ approved: true });
  });
  action($("graph-submit"), async () => {
    const selected = selection.payload();
    if (!selected.length) {
      notify()("请先选择已有稳定身份的笔记。", true);
      return;
    }
    if (!canReplace()) return;
    const fixed = epoch,
      captured = scope(),
      before = snapshot();
    await write(() => graph.submit({ ...captured, selected }));
    if (fixed !== epoch) return;
    if (before !== snapshot() && dirty()) {
      setText(status, uiText("待执行任务已创建，草稿已保留；请手动刷新。"));
      return;
    }
    await loadScope({ approved: true });
  });
  const scopeSwitch = createGraphScopeSwitch({
    current: () => selectedScope,
    restore: (fixed) => {
      client.value = fixed.client_id;
      selectVaults();
      vault.value = fixed.vault_id;
      scopeControls.changed();
    },
    apply: (fixed) => {
      client.value = fixed.client_id;
      selectVaults();
      vault.value = fixed.vault_id;
      scopeControls.changed();
      return loadScope({ approved: true });
    },
    dirty,
    busy: () =>
      pendingReview() || submitting || moc.busy?.() || organization?.busy?.(),
    authorized: (fixed) =>
      authorizedGraphScopes(clients, vaults).some((value) =>
        sameGraphScope(value, fixed),
      ),
    confirm: confirmAction,
    name: (fixed) =>
      `${clients.find((value) => value.id === fixed.client_id)?.name || ""} / ${vaults.find((value) => value.id === fixed.vault_id)?.name || ""}`,
    blocked: () => notify()(msg("graph_scope.busy"), true),
    onError: (error) => notify()(error.message, true),
  });
  function actionLoad() {
    void scopeSwitch.request(scope()).catch(() => {});
  }
  client.addEventListener("change", () => {
    selectVaults();
    actionLoad();
  });
  vault.addEventListener("change", actionLoad);
  filter.addEventListener("change", () => {
    if (!canReplace(root)) {
      filter.value = previousFilter;
      return;
    }
    ((previousFilter = filter.value), (noteOffset = 0), (noteQuery = ""));
    page = 0;
    renderSuggestions();
  });
  function clear({ keepRead = false } = {}) {
    scopeSwitch.cancel({ forget: !keepRead });
    if (!keepRead) {
      reads.cancel();
      scopeControls.reset();
      entered = false;
    }
    setup.stop();
    canvas.clear();
    epoch++;
    observer.stop();
    listing = { items: [] };
    selectedScope = null;
    selection.clear();
    pendingStarts.clear();
    root.replaceChildren();
    notesRoot.replaceChildren();
    jobsRoot.replaceChildren();
    moc.clear();
    organization?.clear();
    setText(status, uiText(""));
    loadReady.hidden = true;
  }
  if (organization) {
    const steps = [...document.querySelectorAll("#graph-panel > .graph-step")];
    organization.attachAnalysis([
      $("graph-toggle"),
      $("graph-policy"),
      setup.host,
      ...steps.slice(0, 2),
    ]);
    organization.attachMoc(steps.slice(2));
  }
  window.addEventListener("pagehide", clear);
  return { refresh, clear };
}
