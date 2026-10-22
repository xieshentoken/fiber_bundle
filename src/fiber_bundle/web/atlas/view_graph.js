// Links & MOC: pick notes → bounded analysis task → review suggestions → MOC write-back.
// Suggestions help find material; accepting one never verifies facts.
import { createGraphSetup } from "../knowledge_graph_setup.js";
import { createOrganizationView } from "../organization.js";
import { featureControls } from "./feature_controls.js";
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  chips,
  codeBlock,
  disclosure,
  empty,
  field,
  input,
  listRow,
  notice,
  segmented,
  select,
  setOptions,
  skeleton,
  steps,
  textarea,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { createTaskObserver } from "../knowledge_graph_observer.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";
import { createMocWriteback } from "./graph_moc.js";
import { createGraphMap } from "./graph_map.js";
import { dateTime } from "./format.js";

const t = strings({
  zh: {
    flow: ["选择笔记", "分析任务", "审核建议", "写回 MOC"],
    identity: "分析身份",
    vault: "知识库",
    noVault: "此身份没有可查询的知识库。",
    enabled: "已启用显式本地分析",
    disabled: "分析已关闭",
    noModel: "未配置语义模型",
    notConfigured:
      "未配置运行环境。请按 README 构建固定本地镜像并保存配置；打开页面不会启动分析。",
    enable: "启用分析",
    disable: "关闭分析",
    notes: "1 · 选择笔记并创建任务",
    notesHelp:
      "未登记笔记须先通过 fiber-notes track 登记稳定身份；此处不会自动登记。",
    filterNotes: "筛选笔记",
    all: "全选",
    none: "取消全选",
    invert: "反选",
    count: "已选 {s} / 可选 {a}；单任务最多 {l} 篇",
    over: "已超限，请减少选择。",
    untracked: "尚未登记稳定身份",
    submit: "创建所选笔记的分析任务",
    needNotes: "请先选择已有稳定身份的笔记。",
    tooMany: "单任务最多 100 篇，请减少选择后提交。",
    jobs: "分析任务",
    noJobs: "尚无分析任务。",
    generation: "当前结果代次：{id}",
    noResult: "尚无结果",
    watching: "正在观察分析任务（最多 3 分钟）",
    job_queued: "待执行",
    job_running: "分析中",
    job_completed: "已完成",
    job_cancelled: "已取消",
    job_interrupted: "已中断",
    job_failed: "失败",
    job_stale: "过期",
    job_launch: "分析启动中",
    run: "执行",
    cancel: "取消",
    quarantine: "核对中断",
    record: "任务记录",
    ready: "新结果已就绪；审核/MOC 草稿已保留。",
    loadReady: "加载新结果",
    ended: "任务已结束：{list}；可手动刷新。",
    timeout: "任务观察已超时，已停止轮询；可手动刷新。",
    gone: "任务已不可用，已停止观察；请刷新核对权限。",
    stopped: "任务观察停止：{m}；可手动刷新。",
    keptDraft: "状态已更新；当前草稿已保留，请手动刷新。",
    suggestions: "2 · 审核建议",
    type: "建议类型",
    t_moc: "MOC 分组",
    t_relation: "关联线索",
    t_entity: "标题提及",
    t_all: "全部",
    list: "建议列表",
    map: "关联图",
    noSuggestions:
      "当前没有可展示的建议。先选择笔记并执行分析；过期索引须重新核对。",
    unavailable: "来源已变化，建议不可用",
    mention: "来源提及",
    s_suggested: "待审核",
    s_accepted: "已接受线索",
    s_rejected: "已拒绝",
    s_stale: "来源已过期",
    method: "方法：{m}",
    score: "模型相似分数 {s}：找资料的线索，需人工核对。",
    lines: "{path} · 第 {a}–{b} 行",
    current: "当前笔记",
    reason: "审核理由",
    reasonHint: "核对来源后填写理由",
    saved: "当前理由：{r} · {time}",
    noReason: "尚无审核理由",
    needReason: "请填写审核理由。",
    accept: "接受线索",
    reject: "拒绝",
    recheck: "重新核对",
    reviewSaved: "审核已保存；接受的是线索，仍须核对原文。",
    reviewFailed: "审核未保存：{m}。输入已保留。",
    prepareMoc: "准备 MOC 写回",
    locate: "刷新并定位",
    ids: "审核记录与标识",
    page: "第 {n} / {m} 页",
    prev: "上一页",
    next: "下一页",
    missing: "原建议已移除、过期或不属于当前筛选。",
    moc: "3 · MOC 写回记录",
    busy: "审核正在保存，请等待完成后再刷新或切换。",
    replace:
      "本页有未保存的内容或 MOC 预览，刷新或切换会清除这些更改。确定继续？",
    lost: "权限或会话失效，已清除当前范围。",
  },
  en: {
    flow: ["Pick notes", "Analyse", "Review suggestions", "Write MOC"],
    identity: "Analyse as",
    vault: "Vault",
    noVault: "This identity has no vault with query access.",
    enabled: "Explicit local analysis enabled",
    disabled: "Analysis off",
    noModel: "no semantic model configured",
    notConfigured:
      "No runtime configured. Build the pinned local image per the README and save the configuration; opening this page never starts analysis.",
    enable: "Enable analysis",
    disable: "Turn analysis off",
    notes: "1 · Pick notes and create a task",
    notesHelp:
      "Untracked notes need a stable identity via fiber-notes track first; nothing is registered here.",
    filterNotes: "Filter notes",
    all: "Select all",
    none: "Select none",
    invert: "Invert",
    count: "{s} selected / {a} selectable; up to {l} per task",
    over: "Over the limit — select fewer.",
    untracked: "no stable identity yet",
    submit: "Create analysis task for selected notes",
    needNotes: "Select notes with a stable identity first.",
    tooMany: "Up to 100 notes per task; select fewer.",
    jobs: "Analysis tasks",
    noJobs: "No analysis tasks yet.",
    generation: "Current result generation: {id}",
    noResult: "none yet",
    watching: "Watching the analysis task (up to 3 minutes)",
    job_queued: "Queued",
    job_running: "Analysing",
    job_completed: "Completed",
    job_cancelled: "Cancelled",
    job_interrupted: "Interrupted",
    job_failed: "Failed",
    job_stale: "Stale",
    job_launch: "Starting",
    run: "Run",
    cancel: "Cancel",
    quarantine: "Mark interrupted",
    record: "Task record",
    ready: "New results are ready; review/MOC drafts were kept.",
    loadReady: "Load new results",
    ended: "Task ended: {list}. Refresh manually.",
    timeout: "Watching timed out and polling stopped. Refresh manually.",
    gone: "The task is no longer available. Refresh and check permissions.",
    stopped: "Watching stopped: {m}. Refresh manually.",
    keptDraft: "State updated; your draft was kept. Refresh manually.",
    suggestions: "2 · Review suggestions",
    type: "Suggestion type",
    t_moc: "MOC groups",
    t_relation: "Link hints",
    t_entity: "Title mentions",
    t_all: "All",
    list: "List",
    map: "Map",
    noSuggestions:
      "No suggestions to show. Pick notes and run an analysis first; stale indexes need re-checking.",
    unavailable: "Source changed; suggestion unavailable",
    mention: "mention",
    s_suggested: "To review",
    s_accepted: "Hint accepted",
    s_rejected: "Rejected",
    s_stale: "Source stale",
    method: "Method: {m}",
    score:
      "Model similarity {s}: a hint for finding material, to check by hand.",
    lines: "{path} · lines {a}–{b}",
    current: "current note",
    reason: "Review reason",
    reasonHint: "Give a reason after checking the source",
    saved: "Current reason: {r} · {time}",
    noReason: "No review reason yet",
    needReason: "Enter a review reason.",
    accept: "Accept hint",
    reject: "Reject",
    recheck: "Re-check",
    reviewSaved:
      "Review saved. You accepted a hint; still check the original text.",
    reviewFailed: "Review not saved: {m}. Your input was kept.",
    prepareMoc: "Prepare MOC write-back",
    locate: "Refresh and locate",
    ids: "Review record and IDs",
    page: "Page {n} / {m}",
    prev: "Previous",
    next: "Next",
    missing: "The suggestion was removed, went stale or isn't in this filter.",
    moc: "3 · MOC write-back records",
    busy: "A review is being saved; wait before refreshing or switching.",
    replace:
      "This page has unsaved input or a MOC preview that refreshing or switching will clear. Continue?",
    lost: "Permission or session lost; the scope was cleared.",
  },
});
const LIMIT = 100;
const PAGE = 20;
export function candidatePage(items, filter, id, page = 0) {
  const filtered = items.filter((i) => filter === "all" || i.type === filter);
  const index = id
    ? filtered.findIndex(
        (i) => i.id === id && i.value && i.review.state !== "stale",
      )
    : -1;
  return {
    filtered,
    page:
      index >= 0
        ? Math.floor(index / PAGE)
        : Math.min(page, Math.max(0, Math.ceil(filtered.length / PAGE) - 1)),
    missing: !!id && index < 0,
  };
}

export function createView(ctx) {
  const graph = ctx.api.graph;
  let epoch = 0,
    clients = [],
    vaults = [],
    profile = null,
    listing = { items: [] },
    page = 0,
    filter = "moc",
    mode = "list",
    fixedScope = null,
    notesData = [];
  const selected = new Map();
  const pendingStarts = new Set();
  const who = select([], undefined, { dataset: { draft: "ignore" } });
  const where = select([], undefined, { dataset: { draft: "ignore" } });
  const policyLine = h("div.a-toolbar");
  const notesHost = h("div.a-stack");
  const jobsHost = h("div.a-rows");
  const status = h("p.a-subtle", { role: "status" });
  const loadReady = button(t("loadReady"), {
    variant: "small",
    iconName: "refresh",
  });
  loadReady.hidden = true;
  const suggestionsHost = h("div.a-stack");
  const scope = () => ({ client_id: who.value, vault_id: where.value });
  let organizationReadError = null,
    recoverySource = null;
  const organization = createOrganizationView(ctx.api.organization, scope, {
    onReadError: (error) => {
      organizationReadError = error;
    },
    onReadSuccess: () => {
      if (recoverySource === "organization") {
        setup.loaded();
        clearRecovery();
      }
      organizationReadError = null;
    },
  });
  const organizationControls = featureControls(organization.host);
  const setup = createGraphSetup(
    graph,
    scope,
    () => refresh({ approved: true }),
    () => profile,
  );
  const setupControls = featureControls(setup.host);
  // Recovery must stay reachable even when the organization tabs cannot load.
  const recoveryHost = h("div", { hidden: true });
  const analysisSetup = h("div", setupControls.node);
  function showRecovery(error, source = "notes") {
    recoverySource = source;
    setup.fail(error);
    recoveryHost.append(setupControls.node);
    recoveryHost.hidden = false;
  }
  function clearRecovery() {
    recoverySource = null;
    analysisSetup.append(setupControls.node);
    recoveryHost.hidden = true;
  }
  setupControls.node.addEventListener("click", (event) => {
    if (event.target.closest('[data-page-target="vaults"]'))
      ctx.navigate("vaults", "detail", { vault: where.value, tab: "organize" });
  });
  const moc = createMocWriteback(graph, scope, () => epoch);
  const map = createGraphMap(graph, scope);
  const pendingReview = () =>
    !!suggestionsHost.querySelector('[data-review-pending="true"]');
  const dirty = () =>
    hasUnsavedChanges(root) ||
    moc.hasPending() ||
    pendingReview() ||
    map.dirty();
  function canReplace() {
    if (pendingReview()) {
      toast(t("busy"), { tone: "warning" });
      return false;
    }
    return (
      !(hasUnsavedChanges(root) || moc.hasPending()) ||
      window.confirm(t("replace"))
    );
  }
  function snapshot() {
    return JSON.stringify(
      [...root.querySelectorAll("input,textarea")]
        .filter((n) => n.dataset.draft !== "ignore")
        .map((n) => n.value),
    );
  }
  function scopedVaults() {
    const grants = clients.find((c) => c.id === who.value)?.grants || [];
    const allowed = vaults.filter(
      (v) =>
        v.status === "active" &&
        grants.some((g) => g.vault_id === v.id && g.can_query),
    );
    setOptions(
      where,
      allowed.map((v) => [v.id, v.name]),
      allowed.some((v) => v.id === where.value) ? where.value : undefined,
    );
  }
  function drawPolicy() {
    const toggle = button(profile?.enabled ? t("disable") : t("enable"), {
      variant: profile?.enabled ? "ghost" : "primary",
      iconName: profile?.enabled ? "stop" : "play",
    });
    toggle.disabled = !profile?.configured;
    toggle.dataset.locked = String(!profile?.configured);
    toggle.addEventListener("click", () =>
      run(toggle, async () => {
        if (!profile?.value || !canReplace()) return;
        const before = snapshot(),
          mine = epoch;
        await graph.savePolicy({
          value: {
            ...profile.value,
            revision: profile.value.revision + 1,
            enabled: !profile.enabled,
          },
          expected_sha256: profile.configuration,
        });
        if (mine !== epoch) return;
        if (before !== snapshot() && dirty()) {
          status.textContent = t("keptDraft");
          return;
        }
        await refresh({ approved: true });
      }),
    );
    clear(
      policyLine,
      profile?.configured
        ? badge(
            `${profile.enabled ? t("enabled") : t("disabled")} · ${profile.value.engine.model?.name || t("noModel")}`,
            profile.enabled ? "success" : "paused",
          )
        : notice(t("notConfigured"), "warning"),
      h("span.a-spacer"),
      toggle,
    );
  }
  // Note selection is scope/version-bound operation state, never an editable draft.
  function drawNotes() {
    const enabled = !!profile?.enabled;
    const available = new Map(
      enabled
        ? notesData
            .filter((n) => n.selectable && n.note_id && n.sha256)
            .map((n) => [n.note_id, n])
        : [],
    );
    for (const [id, sha] of [...selected])
      if (available.get(id)?.sha256 !== sha) selected.delete(id);
    const search = input({
      type: "search",
      placeholder: t("filterNotes"),
      dataset: { draft: "ignore" },
    });
    const count = h("p.a-subtle", { role: "status" });
    const listBox = h("div.a-note-picks");
    const submit = button(t("submit"), {
      variant: "primary",
      iconName: "play",
    });
    const checks = [];
    function update() {
      const over = selected.size > LIMIT;
      count.textContent =
        t("count", { s: selected.size, a: available.size, l: LIMIT }) +
        (over ? " " + t("over") : "");
      const invalid = !enabled || !selected.size || over;
      submit.disabled = invalid;
      submit.dataset.locked = String(invalid);
      for (const check of checks)
        check.checked = selected.has(check.dataset.id);
    }
    for (const note of notesData) {
      const check = h("input", {
        type: "checkbox",
        disabled: !enabled || !note.selectable || !note.note_id || !note.sha256,
        dataset: { draft: "ignore", id: note.note_id || "" },
      });
      check.addEventListener("change", () => {
        if (check.checked) selected.set(note.note_id, note.sha256);
        else selected.delete(note.note_id);
        update();
      });
      checks.push(check);
      listBox.append(
        h(
          "label.a-check",
          {
            dataset: { text: `${note.title} ${note.note_path}`.toLowerCase() },
          },
          check,
          h(
            "span",
            h("strong", note.title),
            " · ",
            h("span.a-subtle", note.note_path),
            note.selectable ? null : h("span.a-subtle", " · " + t("untracked")),
          ),
        ),
      );
    }
    search.addEventListener("input", () => {
      const needle = search.value.trim().toLowerCase();
      for (const label of listBox.children)
        label.hidden = needle && !label.dataset.text.includes(needle);
    });
    const bulk = (operation) => () => {
      for (const [id, note] of available) {
        if (
          operation === "none" ||
          (operation === "invert" && selected.has(id))
        )
          selected.delete(id);
        else selected.set(id, note.sha256);
      }
      update();
    };
    submit.addEventListener("click", () =>
      run(submit, async () => {
        if (selected.size > LIMIT) throw new Error(t("tooMany"));
        const payload = [...selected].map(([note_id, expected_sha256]) => ({
          note_id,
          expected_sha256,
        }));
        if (!payload.length) throw new Error(t("needNotes"));
        if (!canReplace()) return false;
        const mine = epoch,
          captured = scope(),
          before = snapshot();
        await graph.submit({ ...captured, selected: payload });
        if (mine !== epoch) return;
        if (before !== snapshot() && dirty()) {
          status.textContent = t("keptDraft");
          return;
        }
        await loadScope({ approved: true });
      }),
    );
    clear(
      notesHost,
      h("p.a-subtle", t("notesHelp")),
      h(
        "div.a-toolbar",
        search,
        ...["all", "none", "invert"].map((op) =>
          button(t(op), {
            variant: "small",
            disabled: !enabled,
            onClick: bulk(op),
          }),
        ),
      ),
      count,
      listBox,
      h("div.a-form-actions", submit),
    );
    update();
  }
  function drawJobs(tasks, fixed, revision) {
    status.textContent =
      t("generation", { id: listing.job_id || t("noResult") }) +
      (tasks.some((j) => j.state === "running") ? " · " + t("watching") : "");
    const tones = {
      completed: "success",
      failed: "error",
      interrupted: "error",
      stale: "error",
      cancelled: "info",
    };
    clear(
      jobsHost,
      tasks.length
        ? tasks.map((job) => {
            const starting =
              pendingStarts.has(job.id) && job.state === "queued";
            const ops = starting
              ? [["cancel"]]
              : job.state === "queued"
                ? [["start"], ["cancel"]]
                : job.state === "running"
                  ? [["cancel"], ["quarantine"]]
                  : [];
            const actions = ops.map(([op]) => {
              const control = button(op === "start" ? t("run") : t(op), {
                variant: "small",
              });
              control.addEventListener("click", () =>
                run(control, async () => {
                  if (revision !== epoch || !canReplace()) return false;
                  const before = snapshot();
                  const response = await graph.jobAction(job.id, op, fixed);
                  if (
                    op === "start" &&
                    ["launch_requested", "running"].includes(response.state)
                  )
                    pendingStarts.add(job.id);
                  if (revision !== epoch) return;
                  if (before !== snapshot() && dirty()) {
                    status.textContent = t("keptDraft");
                    observer.start([
                      { ...job, state: op === "start" ? "running" : job.state },
                    ]);
                    return;
                  }
                  await loadScope({ approved: true });
                }),
              );
              return control;
            });
            return h(
              "div",
              listRow({
                iconName: "spark",
                title: starting
                  ? t("job_launch")
                  : t("job_" + job.state) === "job_" + job.state
                    ? job.state
                    : t("job_" + job.state),
                detail: [
                  job.error_code,
                  job.created_at ? dateTime(job.created_at) : null,
                ]
                  .filter(Boolean)
                  .join(" · "),
                status: [job.state, tones[job.state] || "waiting"],
                actions,
              }),
              h(
                "div.a-pad",
                disclosure(
                  t("record"),
                  codeBlock(JSON.stringify(job, null, 2)),
                ),
              ),
            );
          })
        : empty(t("noJobs"), { iconName: "spark" }),
    );
  }
  const observer = createTaskObserver({
    read: () => graph.jobs(fixedScope.client_id, fixedScope.vault_id),
    async onUpdate(tasks, watched) {
      for (const task of tasks)
        if (!["queued", "running"].includes(task.state))
          pendingStarts.delete(task.id);
      drawJobs(tasks, { ...fixedScope }, epoch);
      const finished = tasks.filter(
        (j) => watched.includes(j.id) && j.state === "completed",
      );
      const failed = tasks.filter(
        (j) =>
          watched.includes(j.id) &&
          ["failed", "cancelled", "interrupted", "stale"].includes(j.state),
      );
      if (finished.length && !finished.some((j) => j.id === listing.job_id)) {
        if (dirty()) {
          status.textContent = t("ready");
          loadReady.hidden = false;
        } else await loadScope({ automatic: true });
      } else if (failed.length)
        status.textContent = t("ended", {
          list: failed
            .map(
              (j) =>
                t("job_" + j.state) +
                (j.error_code ? " · " + j.error_code : ""),
            )
            .join("、"),
        });
    },
    onStop(reason, error) {
      if (reason === "timeout") status.textContent = t("timeout");
      if (reason === "unavailable") status.textContent = t("gone");
      if (reason === "error") {
        if ([401, 403].includes(error?.status)) {
          clearScope();
          toast(t("lost"), { tone: "error" });
        } else status.textContent = t("stopped", { m: error.message });
      }
    },
  });
  loadReady.addEventListener("click", () =>
    run(loadReady, () => loadScope({ approved: true })),
  );

  function suggestionCard(item, entities) {
    const value = item.value;
    const node = h("article.a-suggestion", {
      dataset: { candidateId: item.id },
    });
    const feedback = h("p.a-subtle", { role: "status" });
    const savedLine = h("p.a-subtle");
    const stateBadge = h("span");
    const title = !value
      ? t("unavailable")
      : item.type === "moc"
        ? value.title
        : item.type === "entity"
          ? value.label
          : `${entities.get(value.source) || t("mention")} → ${entities.get(value.target) || t("mention")}`;
    const tones = {
      suggested: "waiting",
      accepted: "success",
      rejected: "info",
      stale: "error",
    };
    function display() {
      clear(
        stateBadge,
        badge(t("s_" + item.review.state), tones[item.review.state] || "info"),
      );
      savedLine.textContent = item.review.reason
        ? t("saved", {
            r: item.review.reason,
            time: dateTime(item.review.reviewed_at),
          })
        : t("noReason");
    }
    node.append(
      h(
        "header.a-card-head",
        h("div.a-card-titles", h("h3", title), savedLine),
        stateBadge,
      ),
    );
    if (value) {
      const meta = [];
      if (value.method) meta.push(t("method", { m: value.method }));
      if (value.score != null)
        meta.push(t("score", { s: value.score.toFixed(3) }));
      if (meta.length) node.append(h("p.a-subtle", meta.join(" · ")));
      const evidence = Array.isArray(value.evidence)
        ? value.evidence
        : [value.evidence];
      for (const span of evidence) {
        const citation = t("lines", {
          path: item.paths[span.note_id] || t("current"),
          a: span.line,
          b: span.line_end,
        });
        const quote = h("blockquote.a-quote", span.quote);
        node.append(
          span.quote.length > 100 || evidence.length > 3
            ? disclosure(citation, quote)
            : h("div.a-stack", h("p.a-subtle", citation), quote),
        );
      }
      const reason = textarea({
        dataset: { draft: "tracked" },
        maxLength: 400,
        rows: 2,
        value: item.review.reason || "",
        placeholder: t("reasonHint"),
        "aria-label": t("reason"),
      });
      const actions = h("div.a-form-actions");
      let busy = false;
      for (const [label, next, variant] of [
        [t("accept"), "accepted", "primary"],
        [t("reject"), "rejected", null],
        [t("recheck"), "suggested", "ghost"],
      ]) {
        const control = button(label, {
          variant,
          iconName: next === "accepted" ? "check" : null,
        });
        control.addEventListener("click", async () => {
          if (busy) return;
          const mine = epoch,
            text = reason.value.trim(),
            ack = captureDraftSave(node);
          if (!text) {
            feedback.textContent = t("needReason");
            return;
          }
          busy = true;
          node.dataset.reviewPending = "true";
          control.setAttribute("aria-busy", "true");
          try {
            const decision = await graph.review(item.id, {
              ...fixedScope,
              configuration: listing.configuration,
              revision: item.review.revision,
              state: next,
              reason: text,
            });
            if (mine !== epoch) return;
            item.review = decision;
            ack();
            display();
            feedback.textContent = t("reviewSaved");
          } catch (error) {
            if (mine === epoch)
              feedback.textContent = t("reviewFailed", { m: error.message });
          } finally {
            busy = false;
            control.removeAttribute("aria-busy");
            delete node.dataset.reviewPending;
          }
        });
        actions.append(control);
      }
      if (item.type === "moc") {
        const prepare = button(t("prepareMoc"), { iconName: "graph" });
        prepare.addEventListener("click", () =>
          run(prepare, () => moc.open(item)),
        );
        actions.append(prepare);
      }
      node.append(field(t("reason"), reason), actions, feedback);
    }
    const locate = button(t("locate"), {
      variant: "small",
      iconName: "refresh",
    });
    locate.addEventListener("click", () =>
      run(locate, () => loadScope({ anchorId: item.id })),
    );
    node.append(
      h("div.a-toolbar", locate),
      disclosure(
        t("ids"),
        codeBlock(
          JSON.stringify({ id: item.id, review: item.review }, null, 2),
        ),
      ),
    );
    display();
    rememberDraft(node);
    return node;
  }
  function drawSuggestions(anchorId = null) {
    if (mode === "map") return;
    const result = candidatePage(listing.items, filter, anchorId, page);
    page = result.page;
    const entities = new Map(
      listing.items
        .filter((i) => i.type === "entity" && i.value)
        .map((i) => [i.id, i.value.label]),
    );
    const pages = Math.max(1, Math.ceil(result.filtered.length / PAGE));
    clear(
      suggestionsHost,
      result.missing ? notice(t("missing"), "warning") : null,
      result.filtered.length
        ? result.filtered
            .slice(page * PAGE, page * PAGE + PAGE)
            .map((item) => suggestionCard(item, entities))
        : empty(t("noSuggestions"), { iconName: "graph" }),
      result.filtered.length > PAGE
        ? h(
            "div.a-toolbar",
            button(t("prev"), {
              variant: "small",
              disabled: page === 0,
              onClick: () =>
                canReplace() &&
                ((page = Math.max(0, page - 1)), drawSuggestions()),
            }),
            h("span.a-subtle", t("page", { n: page + 1, m: pages })),
            button(t("next"), {
              variant: "small",
              disabled: page >= pages - 1,
              onClick: () =>
                canReplace() &&
                ((page = Math.min(pages - 1, page + 1)), drawSuggestions()),
            }),
          )
        : null,
    );
    if (anchorId)
      suggestionsHost
        .querySelector(`[data-candidate-id="${CSS.escape(anchorId)}"]`)
        ?.scrollIntoView({ block: "center" });
  }
  const typeHost = h("div");
  const modeHost = h("div");
  function drawControls() {
    clear(
      typeHost,
      chips(
        ["moc", "relation", "entity", "all"].map((value) => ({
          value,
          label: t("t_" + value),
        })),
        filter,
        (value) => {
          if (!canReplace()) return;
          filter = value;
          page = 0;
          drawControls();
          drawSuggestions();
        },
        t("type"),
      ),
    );
    typeHost.hidden = mode === "map";
    clear(
      modeHost,
      segmented(
        [
          { value: "list", label: t("list"), icon: "inventory" },
          { value: "map", label: t("map"), icon: "graph" },
        ],
        mode,
        (value) => {
          if (value === mode) return;
          if (mode === "map" && map.dirty() && !window.confirm(t("replace"))) {
            drawControls();
            return;
          }
          mode = value;
          drawControls();
          if (mode === "map") {
            clear(suggestionsHost, map.node);
            void map.load();
          } else {
            map.dispose();
            drawSuggestions();
          }
        },
        t("type"),
      ),
    );
  }
  function clearScope() {
    epoch++;
    observer.stop();
    map.dispose();
    organization.clear();
    listing = { items: [] };
    fixedScope = null;
    selected.clear();
    pendingStarts.clear();
    notesData = [];
    notesHost.replaceChildren();
    jobsHost.replaceChildren();
    suggestionsHost.replaceChildren();
    moc.clear();
    status.textContent = "";
    loadReady.hidden = true;
  }
  async function loadScope({
    automatic = false,
    anchorId = null,
    approved = false,
  } = {}) {
    if (automatic && dirty()) {
      status.textContent = t("ready");
      loadReady.hidden = false;
      return;
    }
    if (!automatic && !approved && !canReplace()) return;
    observer.stop();
    const before = snapshot(),
      revision = ++epoch,
      fixed = scope();
    const same =
      fixedScope?.client_id === fixed.client_id &&
      fixedScope?.vault_id === fixed.vault_id;
    if (!same) {
      selected.clear();
      pendingStarts.clear();
      page = 0;
      moc.clear();
    }
    if (!fixed.client_id || !fixed.vault_id) {
      clearScope();
      clear(suggestionsHost, notice(t("noVault"), "warning"));
      return;
    }
    try {
      organizationReadError = null;
      const organizationRead = organization.refresh();
      const [notes, suggestions, tasks] = await Promise.all([
        graph.notes(fixed.client_id, fixed.vault_id),
        graph.suggestions(fixed.client_id, fixed.vault_id),
        graph.jobs(fixed.client_id, fixed.vault_id),
      ]);
      await organizationRead;
      if (revision !== epoch) return;
      if (
        (pendingReview() || (before !== snapshot() && dirty())) &&
        (automatic || !canReplace())
      ) {
        status.textContent = t("keptDraft");
        loadReady.hidden = false;
        observer.start(
          tasks.map((j) =>
            pendingStarts.has(j.id) && j.state === "queued"
              ? { ...j, state: "launch_requested" }
              : j,
          ),
        );
        return;
      }
      fixedScope = fixed;
      listing = suggestions;
      notesData = notes;
      if (organizationReadError)
        showRecovery(organizationReadError, "organization");
      else {
        setup.loaded();
        clearRecovery();
      }
      loadReady.hidden = true;
      drawNotes();
      drawJobs(tasks, fixed, revision);
      if (mode === "map") {
        clear(suggestionsHost, map.node);
        await map.load();
      } else drawSuggestions(same ? anchorId : null);
      await moc.refresh(fixed, revision);
      if (revision === epoch)
        observer.start(
          tasks.map((j) =>
            pendingStarts.has(j.id) && j.state === "queued"
              ? { ...j, state: "launch_requested" }
              : j,
          ),
        );
    } catch (error) {
      showRecovery(error);
      if (revision === epoch && [401, 403].includes(error.status)) clearScope();
      throw error;
    }
  }
  async function refresh({ approved = false } = {}) {
    if (!approved && !canReplace()) return;
    observer.stop();
    const revision = ++epoch;
    const [nextClients, nextVaults, state] = await Promise.all([
      ctx.store.clients(),
      ctx.store.vaults(),
      graph.policy(),
    ]);
    if (revision !== epoch) return;
    clients = nextClients.filter((c) => c.active);
    vaults = nextVaults;
    profile = state;
    setOptions(
      who,
      clients.map((c) => [c.id, c.name]),
      clients.some((c) => c.id === who.value) ? who.value : undefined,
    );
    scopedVaults();
    drawPolicy();
    drawControls();
    await loadScope({ approved: true });
  }
  function scopeChanged() {
    if (!canReplace()) {
      if (fixedScope) {
        who.value = fixedScope.client_id;
        scopedVaults();
        where.value = fixedScope.vault_id;
      }
      return;
    }
    loadScope({ approved: true }).catch((error) =>
      toast(error.message, { tone: "error" }),
    );
  }
  who.addEventListener("change", () => {
    scopedVaults();
    scopeChanged();
  });
  where.addEventListener("change", scopeChanged);
  const analysisCards = [
    analysisSetup,
    card({ title: t("notes") }, notesHost),
    card(
      { title: t("jobs"), className: "is-flush", actions: loadReady },
      h("p.a-subtle.a-pad", status),
      jobsHost,
    ),
    card(
      {
        title: t("suggestions"),
        actions: h("div.a-toolbar", typeHost, modeHost),
      },
      suggestionsHost,
    ),
  ];
  organization.attachAnalysis(analysisCards);
  organization.attachMoc([card({ title: t("moc") }, moc.list)]);
  const root = h(
    "div.a-stack",
    steps(t("flow"), 0),
    card(
      {},
      h("div.a-form-grid", field(t("identity"), who), field(t("vault"), where)),
    ),
    recoveryHost,
    organizationControls.node,
  );
  // The analysis enable control belongs to its Atlas analysis workspace.
  analysisCards[1].prepend(policyLine);
  clear(suggestionsHost, skeleton(4));
  window.addEventListener("pagehide", () => observer.stop());
  return {
    node: root,
    refresh: () => refresh({ approved: true }),
    dirty: () =>
      organization.dirty() ||
      moc.hasPending() ||
      pendingReview() ||
      map.dirty(),
    dispose() {
      epoch++;
      observer.stop();
      organization.clear();
      organizationControls.dispose();
      setupControls.dispose();
      map.dispose();
    },
  };
}
