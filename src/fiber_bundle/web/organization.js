import {
  appendRetainedMembers,
  appendCatalogObservation,
} from "./organization_catalog_feedback.js";
import { joinText, msg, phrase, setText, uiText } from "./i18n/index.js";
import { classificationActions } from "./organization_task_controls.js";
// Organization view: scope-bound policy drafts, exceptions and native Skill membership.
import { el } from "./ui.js";
import { scopedRead } from "./organization_refresh_request.js";
import { maintenanceSection } from "./organization_maintenance.js";
import { executionProgress } from "./organization_progress_view.js";
import { control } from "./record_ui.js";
import { organizationSettings } from "./organization_settings.js";
import { createSections, disclosure, retainView } from "./view_sections.js";
import {
  readOrganizationCycle,
  progressRetryDelay,
  organizationWorking,
} from "./organization_read_cycle.js";
import { coverageNotice } from "./organization_coverage.js";
import { memberControls, renderMemberResult } from "./organization_members.js";
import { createMemberSession } from "./organization_member_session.js";
import {
  maintenancePhase,
  previewInvalidated,
} from "./organization_progress.js";

const states = {
  new: "未分类",
  linked: "已归类",
  review: "待复核",
  unclassified: "未找到合适分类",
  queued: "排队中",
  running: "处理中",
  preview: "预览待确认",
  completed: "完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
  blocked: "处理暂停",
};
export function createOrganizationView(
  api,
  scope,
  { onReadError = () => {}, onReadSuccess = () => {}, onOpenIndex } = {},
) {
  const maintenanceDraft = { mode: "quick", title: "", topic: "", onOpenIndex };
  const host = el("section", null, "record-card"),
    message = el("p"),
    freshness = el("p", "", "muted"),
    memberResult = el("aside", null, "record-card"),
    body = el("div");
  message.setAttribute("role", "status");
  host.classList.add("organization-workspace");
  memberResult.hidden = true;
  memberResult.setAttribute("role", "status");
  memberResult.setAttribute("aria-live", "polite");
  host.append(message, freshness, memberResult, body);
  const membersSession = createMemberSession({
    scope,
    submit: (request) => api.members(request),
    refresh: () => refresh(),
    changed: (result, unresolved) =>
      renderMemberResult(memberResult, result, () => refresh(), unresolved),
    denied: (error) => {
      clear();
      onReadError(error);
      setText(message, uiText(error.message));
    },
  });
  const analysisHost = el("div"),
    mocHost = el("div");
  const parking = el("div");
  parking.hidden = true;
  parking.append(analysisHost, mocHost);
  host.append(parking);
  let selectedSection = "maintenance",
    lastResult = "";
  let epoch = 0,
    offset = 0,
    query = "",
    searchDraft = "",
    exceptions = true,
    dirty = false,
    fixedKey = "";
  let poll = null,
    lastData = null,
    retryCount = 0,
    deferredPaint = false,
    catalogChanged = false,
    catalogReady = false,
    catalogError = null,
    readGeneration = 0,
    writing = 0;
  function interacting() {
    return (
      membersSession.busy() ||
      host.querySelector('[role="combobox"][aria-expanded="true"]') ||
      (host.contains(document.activeElement) &&
        document.activeElement.matches("input,textarea,select,[role=combobox]"))
    );
  }
  const requestRead = scopedRead(refreshNow);
  function refresh(options = {}) {
    if (options.force) readGeneration++;
    return requestRead({
      ...options,
      key: JSON.stringify(scope()) + ":" + readGeneration,
    });
  }
  function paint(result, fixed) {
    retainView(body, () => {
      if (catalogReady) render(result, fixed);
      else {
        // Analysis/MOC controls are shared with the graph view and must survive
        // a catalog failure just as they survive an ordinary catalog repaint.
        parking.append(analysisHost, mocHost);
        body.replaceChildren(
          maintenanceSection(
            {
              ...result,
              mocs: [],
            },
            fixed,
            api,
            refresh,
            maintenanceDraft,
            { catalogReady: false, catalogError },
          ),
        );
        appendRetainedMembers(body, result.retained_member_issues);
        appendCatalogObservation(body, catalogError);
      }
    });
  }
  async function refreshNow({ automatic = false, force = false } = {}) {
    const fixed = scope(),
      key = JSON.stringify(fixed),
      revision = ++epoch;
    if (!fixed.client_id || !fixed.vault_id) {
      clear();
      setText(
        message,
        uiText("请选择有访问权限的身份与知识库，查看整理任务和操作。"),
      );
      return;
    }
    if (fixedKey !== key) {
      membersSession.activate(fixed);
      Object.assign(maintenanceDraft, { mode: "quick", title: "", topic: "" });
      parking.append(analysisHost, mocHost);
      body.replaceChildren();
      selectedSection = "maintenance";
      lastResult = "";
      lastData = null;
      catalogReady = false;
      catalogChanged = false;
      catalogError = null;
      retryCount = 0;
      maintenanceDraft.views = {};
      delete maintenanceDraft.inboxReview;
      delete maintenanceDraft.submissions;
      delete maintenanceDraft.confirmations;
      delete maintenanceDraft.previewTextSaved;
      delete maintenanceDraft.previewTextEdited;
      delete maintenanceDraft.previewSubmitting;
      maintenanceDraft.closePanels = new Map();
      dirty = false;
      offset = 0;
      query = searchDraft = "";
      fixedKey = key;
    }
    if (dirty && !automatic) {
      setText(message, uiText("配置草稿已保留；保存后可刷新任务。"));
      return;
    }
    if (!automatic) retryCount = 0;
    try {
      const cycle = await readOrganizationCycle(api, fixed, lastData, {
        automatic,
        offset,
        query,
        exceptions,
      });
      if (revision !== epoch || JSON.stringify(scope()) !== key) return;
      const result = cycle.data;
      if (cycle.catalog === true) {
        catalogReady = true;
        catalogChanged = false;
        catalogError = null;
      } else if (cycle.catalog === false) {
        catalogReady = false;
        catalogChanged = true;
        catalogError = cycle.error;
        onReadError(cycle.error);
      }
      retryCount = 0;
      if (revision !== epoch || JSON.stringify(scope()) !== key) return;
      if (interacting() || dirty) {
        const job =
          result.jobs.find((j) =>
            ["queued", "running", "cancelling"].includes(j.state),
          ) || result.jobs.find((j) => j.maintenance);
        if (job?.maintenance) {
          const stats = executionProgress(job);
          setText(
            message,
            phrase("后台进度：{p0} · {p1}/{p2} 项", {
              p0: uiText(maintenancePhase(job)),
              p1: stats.completed,
              p2: stats.total,
            }),
          );
        }
        deferredPaint = true;
        lastData = result;
        schedule(result);
        return;
      }
      if (revision !== epoch || JSON.stringify(scope()) !== key) return;
      const stamp = JSON.stringify([
        result,
        result.jobs
          .filter((job) => previewInvalidated(job))
          .map((job) => job.id),
      ]);
      if (force || !automatic || stamp !== lastResult) paint(result, fixed);
      else
        setText(
          message,
          catalogReady
            ? phrase("{p0}：{p1} 项", {
                p0: exceptions ? uiText("待分类与异常") : uiText("全部知识"),
                p1: result.total,
              })
            : catalogReadMessage(),
        );
      if (catalogReady && !catalogChanged) onReadSuccess();
      lastResult = stamp;
      lastData = result;
      setText(
        freshness,
        catalogChanged
          ? catalogReadMessage()
          : phrase("更新于 {p0}", { p0: new Date().toLocaleTimeString() }),
      );
      deferredPaint = false;
      schedule(result);
    } catch (error) {
      if (revision !== epoch || JSON.stringify(scope()) !== key) return;
      onReadError(error);
      if ([401, 403, 404].includes(error.status)) {
        membersSession.clear();
        fixedKey = "";
        maintenanceDraft.closePanels = new Map();
        delete maintenanceDraft.inboxReview;
        delete maintenanceDraft.submissions;
        delete maintenanceDraft.confirmations;
        delete maintenanceDraft.previewTextSaved;
        delete maintenanceDraft.previewTextEdited;
        delete maintenanceDraft.previewSubmitting;
        parking.append(analysisHost, mocHost);
        body.replaceChildren();
        clearTimeout(poll);
        lastData = null;
      }
      setText(
        message,
        phrase("归类状态读取失败：{p0}", { p0: uiText(error.message) }),
      );
      if (![401, 403, 404].includes(error.status)) {
        clearTimeout(poll);
        const delay = progressRetryDelay(retryCount++);
        if (delay !== null)
          poll = setTimeout(() => refresh({ automatic: true }), delay);
        else setText(freshness, uiText("自动读取已暂停，请手动刷新重试。"));
      }
    }
  }
  function catalogReadMessage() {
    return catalogError?.message
      ? phrase("目录读取未完成：{p0}；任务进度仍按后台记录显示。", {
          p0: String(uiText(catalogError.message)).replace(
            /[。.!！？?]+$/u,
            "",
          ),
        })
      : uiText("任务进度可继续更新；目录读取暂不可用，请手动刷新重试。");
  }
  function render(result, fixed) {
    parking.append(analysisHost, mocHost);
    body.replaceChildren();
    appendRetainedMembers(body, result.retained_member_issues);
    dirty = false;
    const work = async (fn) => {
      const key = JSON.stringify(scope());
      let result;
      writing++;
      try {
        result = await fn();
      } finally {
        writing--;
      }
      if (key === JSON.stringify(scope())) await refresh();
      return result;
    };
    setText(
      message,
      phrase("{p0}：{p1} 项", {
        p0: exceptions ? uiText("待分类与异常") : uiText("全部知识"),
        p1: result.total,
      }),
    );
    const config = organizationSettings(result, fixed, api, work, {
      onDirty: () => {
        dirty = true;
      },
      onClean: () => {
        dirty = false;
      },
      refresh,
    });
    const members = el("div"),
      history = el("div");
    const actions = el("div", null, "panel-actions"),
      search = el("input");
    search.value = searchDraft;
    search.addEventListener("input", () => {
      searchDraft = search.value;
    });
    setText(search, uiText("查找标题、路径或ID"), "placeholder");
    actions.append(
      control(exceptions ? "查看全部知识" : "只看待分类与异常", async () => {
        exceptions = !exceptions;
        offset = 0;
        await refresh();
      }),
      search,
      control("查找", async () => {
        query = search.value;
        offset = 0;
        await refresh();
      }),
      control("预览 Inbox 归类", () =>
        work(() => api.submit({ ...fixed, target: "Inbox", preview: true })),
      ),
      control("预览历史归类", () =>
        work(() => api.submit({ ...fixed, target: "全库", preview: true })),
      ),
    );
    const coverage = coverageNotice(
      result.coverage,
      "organization-coverage",
      false,
      { scope: fixed, api },
    );
    if (coverage) members.append(coverage);
    members.append(actions);
    for (const item of result.items) {
      const card = el("article", null, "record-card");
      card.append(
        el("h3", item.title),
        el(
          "p",
          joinText([
            "",
            item.kind === "expert" ? uiText("专家 Skill") : uiText("普通笔记"),
            " · ",
            states[item.status] || item.status,
            " · ",
            item.path,
            "",
          ]),
        ),
        el(
          "p",
          phrase("当前 MOC：{p0}", {
            p0:
              item.moc_ids
                .map((id) => result.mocs.find((m) => m.id === id)?.title || id)
                .join("、") || "未挂载",
          }),
        ),
        memberControls(item, result.mocs, membersSession),
      );
      members.append(card);
    }
    const pages = el("div", null, "panel-actions");
    if (offset)
      pages.append(
        control("上一页", async () => {
          offset = Math.max(0, offset - 100);
          await refresh();
        }),
      );
    if (result.next_offset !== null)
      pages.append(
        control("下一页", async () => {
          offset = result.next_offset;
          await refresh();
        }),
      );
    members.append(pages);
    for (const job of result.jobs.filter((j) => !j.maintenance)) {
      const card = el("article", null, "record-card");
      card.append(
        el(
          "p",
          `${states[job.state] || job.state} · ${job.completed}/${job.items.length} · ${job.id}`,
        ),
      );
      const reason = (code) =>
        ({
          processing_cloud_content_not_allowed:
            "正文发送权限未开启，模型补判暂停。",
          processing_model_unconfigured: "未配置处理模型，模型补判暂停。",
          model_unconfigured: "未配置处理模型，模型补判暂停。",
          model_key_budget_exhausted: "统一模型消耗预算不足，补足后可继续。",
          model_price_unknown_use_token_budget:
            "该模型暂无价格信息，请在统一消耗设置中使用 Token 预算。",
          organization_model_result_unknown:
            "上次调用结果未知，已保留消耗记录；不会自动重复发送，请先核对模型调用。",
          organization_model_evidence_invalid:
            "模型返回的分类证据无法对应原文，尚未写入关系。",
          organization_model_response_invalid:
            "模型未返回可用正文，已保留调用和消耗记录。",
          organization_legacy_plan_replan_required:
            "这是旧版未完成计划，请发送 /归类 创建新版计划。",
        })[code] || code;
      if (job.error) card.append(el("p", reason(job.error), "error"));
      if (job.reason) card.append(el("p", uiText(job.reason)));
      if (job.index_state)
        card.append(
          el(
            "p",
            phrase("检索索引：{p0}{p1}", {
              p0:
                { pending: "等待更新", ready: "已更新", failed: "更新失败" }[
                  job.index_state
                ] || job.index_state,
              p1: job.index_error ? " · " + job.index_error : "",
            }),
          ),
        );
      if (job.resume_mode === "cached_writeback")
        card.append(
          el("p", msg("organization.recovery.cached_intro"), "muted"),
        );
      if (job.taxonomy?.requires_review) {
        const review = el("details");
        review.append(el("summary", uiText("分类体系建议（需单独人工确认）")));
        for (const key of [
          "create_candidates",
          "split_candidates",
          "merge_candidates",
        ])
          for (const proposal of job.taxonomy[key] || [])
            review.append(
              el(
                "p",
                uiText(
                  (proposal.name ||
                    proposal.moc ||
                    proposal.mocs?.join("、") ||
                    "分类建议") +
                    "：" +
                    (proposal.reason || proposal.action),
                ),
              ),
            );
        card.append(review);
      }
      for (const item of job.items)
        if (item.error || job.state === "preview" || item.phase)
          card.append(
            el(
              "p",
              joinText([
                "",
                item.title,
                "：",
                (item.error && reason(item.error)) ||
                  (item.phase &&
                    {
                      semantic: "本地语义召回",
                      model: "模型核对正文",
                      ready: "证据已校验",
                    }[item.phase]) ||
                  item.candidates
                    .map(
                      (c) =>
                        (result.mocs.find((m) => m.id === c.moc_id)?.title ||
                          c.moc_id) +
                        "（" +
                        c.reason +
                        "）",
                    )
                    .join("、") ||
                  "待人工判断",
                "",
              ]),
            ),
          );
      for (const { operation, label } of classificationActions(job))
        card.append(
          control(label, () =>
            work(() => api.action(job.id, operation, fixed)),
          ),
        );
      history.append(card);
    }
    history.append(
      el(
        "p",
        uiText(
          "飞书发送 /归类 即可处理未分类资料；再次发送可继续暂停任务。模型消耗沿用统一配置。专家包按包ID和版本供 Agent 调用。",
        ),
        "muted",
      ),
    );

    const past = disclosure("归类任务与消息指令记录", history);
    past.dataset.viewKey = "organization-command-history";
    createSections(
      body,
      [
        {
          id: "maintenance",
          label: "整理笔记",
          nodes: [
            maintenanceSection(
              result,
              fixed,
              api,
              (options) => refresh(options),
              maintenanceDraft,
            ),
            past,
          ],
        },
        { id: "members", label: "MOC 管理", nodes: [members, mocHost] },
        { id: "analysis", label: "关联分析", nodes: [analysisHost] },
        { id: "settings", label: "设置", nodes: [config] },
      ],
      {
        id: "organization",
        selected: selectedSection,
        onSelect: (key) => {
          selectedSection = key;
        },
      },
    );
  }

  function schedule(result) {
    clearTimeout(poll);
    const working = organizationWorking(result);
    const delay = working ? 1500 : null;
    if (delay !== null)
      poll = setTimeout(
        () => {
          if (
            document
              .querySelector('[data-tab="graph"]')
              ?.getAttribute("aria-current") === "page"
          )
            refresh({ automatic: true });
        },
        Math.max(25, delay),
      );
  }
  function clear() {
    membersSession.clear();
    readGeneration++;
    catalogReady = false;
    lastData = null;
    epoch++;
    clearTimeout(poll);
    retryCount = 0;
    deferredPaint = false;
    catalogChanged = false;
    catalogError = null;
    parking.append(analysisHost, mocHost);
    body.replaceChildren();
    setText(message, uiText(""));
    setText(freshness, "");
    dirty = false;
    fixedKey = "";
    query = searchDraft = "";
    Object.assign(maintenanceDraft, {
      mode: "quick",
      title: "",
      topic: "",
      views: {},
      closePanels: new Map(),
    });
    delete maintenanceDraft.inboxReview;
    delete maintenanceDraft.submissions;
    delete maintenanceDraft.confirmations;
    delete maintenanceDraft.previewTextSaved;
    delete maintenanceDraft.previewTextEdited;
    delete maintenanceDraft.previewSubmitting;
    selectedSection = "maintenance";
    lastResult = "";
  }
  host.addEventListener("focusout", () => {
    if (deferredPaint && !dirty) {
      clearTimeout(poll);
      poll = setTimeout(() => refresh({ automatic: true }), 0);
    }
  });
  return {
    host,
    refresh,
    clear,
    dirty: ({ manual = true } = {}) =>
      dirty ||
      membersSession.busy() ||
      (manual && membersSession.dirty()) ||
      !!maintenanceDraft.previewTextEdited,
    busy: () =>
      maintenanceDraft.previewSubmitting ||
      writing > 0 ||
      membersSession.busy() ||
      [...(maintenanceDraft.submissions?.values() || [])].some(
        (state) => state.pending,
      ) ||
      [...(maintenanceDraft.inboxReview?.panels?.values() || [])].some(
        (state) =>
          ["applying_operation", "checking_operation", "generating"].includes(
            state.busy,
          ),
      ),
    attachAnalysis: (nodes) => analysisHost.append(...nodes),
    attachMoc: (nodes) => mocHost.append(...nodes),
  };
}
