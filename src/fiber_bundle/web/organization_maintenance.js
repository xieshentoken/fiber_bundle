import {
  appendInboxReview,
  appendInboxReviewHistory,
} from "./organization_inbox_review.js";
import {
  appendArchivePreview,
  appendArchiveIssue,
  appendArchiveRecovery,
  appendArchiveDependencyFailure,
} from "./organization_archive_readiness.js";
import { mountMaintenanceWorkspace } from "./organization_workspace.js";
import { createMaintenancePreviewForm } from "./organization_preview_form.js";
import { appendContinuity } from "./organization_continuity.js";
import { appendItemStages, appendStageSummary } from "./organization_stages.js";
import { appendExecutionStatus } from "./organization_execution_status.js";
import { appendExecutionProgress } from "./organization_progress_view.js";
import { historicalItemFailure } from "./organization_diagnostics.js";
import { partialTaskStatus } from "./organization_partial_scope.js";
// One-off maintenance presentation. Every write still uses the bound backend confirmation.
import { el } from "./ui.js";
import { control, showJSON, recordHeading } from "./record_ui.js";
import { coverageNotice } from "./organization_coverage.js";
import {
  appendCandidateCoverage,
  emptyCandidatePreview,
} from "./organization_candidate_coverage.js";
import {
  appendRecordedPaths,
  appendTaskRecord,
  taskEnded,
} from "./organization_task_record.js";
import { requestErrors } from "./request_errors.js";
import { disclosure } from "./view_sections.js";
import { joinText, phrase, setText, uiText } from "./i18n/index.js";
import {
  modeNames,
  jobNames,
  itemCategory,
  maintenanceSummary,
  maintenancePhase,
  orderedMaintenance,
  previewInvalidated,
  evidenceFailure,
  manuallyResolved,
  verifiedArchive,
} from "./organization_progress.js";
import {
  appendTaskControls,
  maintenanceSubmission,
  bindMaintenanceSubmission,
  setMaintenanceSubmission,
  unknownOutcome,
} from "./organization_task_controls.js";
const descriptions = {
  quick:
    "本地快速整理 Inbox，默认不调用模型。复用有效分类结果，可靠归属按保存规则归档；专家 Draft 按整包处理。",
  additive: "补充 MOC 归属，保留已有关系，不移动文件。",
  deep: "重新评估归属，可替换未锁定的普通人工关系；锁定与拒绝保持，不移动文件。",
};
const labels = {
  changed: "有变更",
  review: "待复核",
  error: "异常",
  waiting: "等待处理",
  unchanged: "无需变更",
  processed: "已人工处理",
  all: "全部",
};
function explain(code, detail) {
  if (requestErrors[code]) return requestErrors[code][0];
  if (code === "organization_model_result_unknown")
    return unknownOutcome(detail);
  const reason = evidenceFailure(detail);
  if (code === "organization_model_evidence_invalid" && reason)
    return `${reason}。分析已暂停，原关系与模型回复保留，不会自动重发请求。`;
  return (
    {
      cloud_item_pending_stale_or_conflicted:
        "文件同步或版本校验未通过，整理已停止。请核对中断位置后恢复未完成项。",
      cloud_metadata_unavailable:
        "暂时无法核对 iCloud 元数据，当前步骤已停止。",
      maintenance_undo_blob_unavailable:
        "撤销所需的私有快照缺失或损坏。原资料保持，请核对备份。",
      maintenance_undo_file_conflict:
        "笔记或 Map 在整理后被修改。用户修改已保留，请核对后继续撤销。",
      maintenance_undo_move_conflict:
        "原路径已被占用，或移动后的资料有变化。资料已保留，请核对后继续撤销。",
      maintenance_undo_relationship_conflict:
        "关系或登记信息在整理后被修改。人工修改已保留，请核对后继续撤销。",
      maintenance_undo_original_unavailable:
        "原文件恢复副本不可用，已停止撤销，请查看任务诊断。",
      maintenance_undo_legacy_journal_missing:
        "旧任务缺少撤销日志，不能自动撤销已应用变更。资料和历史保持。",
      maintenance_other_task_unfinished:
        "本知识库还有未完成的应用或撤销任务，请先完成或取消该任务。",
      maintenance_archive_target_conflict:
        "归档目标已存在或不在允许范围。源文件保留，请核对目标位置后恢复。",
      maintenance_archive_link_review_required:
        "引用存在缺失或歧义，归档已暂停。请先核对链接。",
      maintenance_note_changed: "笔记在预览后发生变化，请重新生成预览。",
      maintenance_preview_changed:
        "预览所依据的资料或 MOC 已变化，请重新生成预览。",
      maintenance_moc_changed:
        "MOC 已被修改，当前写入已停止。请核对后重新预览。",
      maintenance_moc_managed_block_edited:
        "Map 的系统关联区块有人工改动，已保留原文并停止写入。请展开诊断核对具体 Map。",
      maintenance_moc_pending_write:
        "Map 存在尚未核对的写入记录，已停止新增关系。请先核对该 Map 的写入状态。",
      maintenance_manual_decision_changed:
        "人工关系或锁定已变化，当前写入已停止。请重新预览。",
      maintenance_cache_binding_changed:
        "原文、权限、分析配置或 MOC 定义已变化，旧缓存不能安全复用。请按当前资料重新生成整理预览。",
      organization_model_cache_incomplete:
        "已有回复未覆盖全文，保留资料与缓存。本轮仅核对缓存，不自动发送缺少的模型请求。",
      maintenance_authorization_or_policy_changed:
        "权限、保存规则或分析配置已变化，请重新预览。",
      maintenance_archive_binding_changed:
        "归档依据已变化。请核对任务，原文件保留。",
      processing_model_unconfigured:
        "未配置正文处理模型。原关系保留，请到模型与查询核对配置。",
      processing_cloud_content_not_allowed:
        "尚未允许向模型发送正文。原关系保留，请核对正文发送配置。",
      organization_model_result_unknown:
        "上次模型调用结果未知。请先核对调用记录，避免重复发送。",
      model_key_budget_exhausted:
        "统一模型预算不足。请到执行与费用核对消耗配置后继续。",
      organization_model_evidence_invalid:
        "此前分类证据核对中断，原关系保留。恢复时会重验已有回复；仍有异常的条目单独保留核对，继续其余项目，不自动重发请求。",
      organization_model_response_invalid:
        "模型未返回可用分类结果，分析已暂停。请核对模型调用记录。",
    }[code] || "处理未完成，请展开诊断详情核对原因后继续。"
  );
}
export function maintenanceSection(
  result,
  fixed,
  api,
  refresh,
  draft = { mode: "quick", title: "", topic: "" },
  readiness = {},
) {
  draft.views ||= {};
  draft.closePanels ||= new Map();
  const host = el("section", null, "record-card");
  host.id = "organization-maintenance";
  setText(host, uiText("整理笔记"), "aria-label");
  let preparation;
  const jobs = orderedMaintenance(result.jobs),
    current = jobs[0];
  const names = (ids) =>
    (ids || [])
      .map(
        (id) =>
          result.mocs.find((m) => m.id === id)?.title ||
          result.jobs.flatMap((j) => j.new_mocs || []).find((m) => m.id === id)
            ?.title ||
          "未命名 MOC",
      )
      .join("、") || "无";
  function renderJob(job) {
    const submission = maintenanceSubmission(draft, job.id);
    const stats = maintenanceSummary(job),
      planning = job.stage === "planning",
      emptyReport = emptyCandidatePreview(job),
      card = el("article", null, "maintenance-job");
    card.dataset.maintenanceJob = job.id;
    card.append(
      recordHeading(
        joinText(
          [
            uiText(modeNames[job.mode]),
            uiText(
              emptyReport ? "候选检查 · 无可执行条目" : maintenancePhase(job),
            ),
          ],
          " · ",
        ),
        emptyReport
          ? "无需确认"
          : previewInvalidated(job)
            ? "预览已失效"
            : job.state === "waiting_file" &&
                job.items.some((i) => i.archive_wait)
              ? "归档待核对"
              : jobNames[job.state] || "状态待核对",
        ["blocked", "failed", "partial"].includes(job.state)
          ? "waiting"
          : job.state === "completed"
            ? "success"
            : "info",
      ),
    );
    appendTaskRecord(card, job);
    const inspectItem = (parent, path) =>
      appendInboxReview(parent, { job, path, fixed, api, refresh, draft });
    appendCandidateCoverage(card, job, inspectItem);
    card.append(
      el(
        "p",
        uiText(descriptions[job.mode]),
        job.mode === "deep" ? "task-impact" : "muted",
      ),
    );
    const coverage = coverageNotice(job.coverage, `coverage:${job.id}`, false, {
      scope: fixed,
      api,
    });
    if (coverage) card.append(coverage);
    card.append(partialTaskStatus(job, fixed, api, refresh));
    if (job.application_blocked)
      card.append(
        el(
          "p",
          uiText(
            requestErrors[job.application_blocked]?.[0] ||
              job.application_blocked,
          ),
          "task-impact",
        ),
      );
    appendExecutionProgress(card, job);
    if (job.mode === "quick") {
      const counts = { local: 0, cache: 0, model: 0, pending: 0 };
      for (const i of job.items) {
        if (!i.evaluated) continue;
        if (["cache", "model_cache"].includes(i.analysis_origin))
          counts.cache++;
        else if (i.model_used) counts.model++;
        else counts.local++;
        if (!manuallyResolved(i) && !i.selected?.length && !i.existing_reliable)
          counts.pending++;
      }
      card.append(
        el(
          "p",
          phrase(
            "本地核对 {p0} · 缓存复用 {p1} · 模型补充 {p2} · 未解决 {p3}",
            {
              p0: counts.local,
              p1: counts.cache,
              p2: counts.model,
              p3: counts.pending,
            },
          ),
          "muted",
        ),
      );
    }
    appendExecutionStatus(card, job, explain);
    appendStageSummary(card, job);
    appendArchivePreview(card, job);
    appendArchiveDependencyFailure(card, job.preview_validity);
    appendArchiveRecovery(card, job, fixed, api, refresh);
    appendContinuity(card, job, fixed, api, refresh, draft.closePanels);
    if (job.mode === "deep")
      card.append(
        el(
          "p",
          phrase(
            "本次差异：新增 {p0} 条归属，移除 {p1} 条；涉及普通人工归属 {p2} 项。锁定与拒绝关系保持。",
            { p0: stats.additions, p1: stats.removals, p2: stats.manual },
          ),
          "task-impact",
        ),
      );
    if (job.state === "preview" && !emptyCandidatePreview(job))
      card.append(
        el(
          "p",
          uiText(
            "确认后应用本次可靠的归属与目录变更；待复核及无需变更的条目保留原关系。",
          ),
          "muted",
        ),
      );
    if (job.new_mocs?.length)
      card.append(
        el(
          "p",
          phrase("将新建 MOC：{p0}", {
            p0: job.new_mocs.map((m) => m.title || m.path).join("、"),
          }),
          "task-impact",
        ),
      );
    const action = (operation) => async () => {
      if (operation === "confirm" && previewInvalidated(job)) {
        await refresh();
        throw new Error(uiText("预览已失效，请先重新核对预览。"));
      }
      if (submission.pending) return;
      setMaintenanceSubmission(submission, operation);
      try {
        try {
          await api.maintenanceAction(job.id, operation, {
            ...fixed,
            plan_sha256:
              operation === "verify_committed_archives"
                ? job.archive_verification?.plan_sha256
                : job.plan_sha256 || null,
          });
        } catch (error) {
          appendArchiveDependencyFailure(card, error);
          if (error.diagnostic)
            card.append(el("p", error.message, "task-failure"));
          if (
            ["recheck", "confirm"].includes(operation) &&
            [
              "maintenance_preview_changed",
              "maintenance_authorization_or_policy_changed",
            ].includes(error.code)
          ) {
            await refresh();
            preparation.open = true;
            throw new Error(uiText(explain(error.code)), { cause: error });
          }
          throw error;
        }
        await refresh({
          automatic: ["pause", "resume", "confirm", "cancel"].includes(
            operation,
          ),
        });
      } finally {
        setMaintenanceSubmission(submission, null);
      }
    };
    const actions = el("div", null, "task-actions");
    if (job.state === "preview" && !emptyCandidatePreview(job)) {
      if (
        !previewInvalidated(job) &&
        job.items.some(
          (i) =>
            !manuallyResolved(i) &&
            i.analysis_error === "organization_model_evidence_invalid",
        )
      )
        actions.append(control("重新核对异常项", action("recheck")));
      draft.confirmations ||= new Map();
      const confirmationKey = `${job.id}:${job.plan_sha256 || ""}`;
      const accept = el("input"),
        consent = el(
          "label",
          job.mode === "deep"
            ? uiText("我已核对新增、移除及普通人工归属的替换；锁定与拒绝保持。")
            : uiText("我已核对本次归属、新建 MOC 和目录变化。"),
        );
      accept.type = "checkbox";
      accept.dataset.maintenanceConsent = job.id;
      consent.prepend(accept);
      const button = control("确认并执行本次整理", action("confirm"));
      button.className = "primary";
      accept.checked = draft.confirmations.get(confirmationKey) === true;
      button.disabled =
        !accept.checked ||
        previewInvalidated(job) ||
        !!job.application_blocked ||
        !!job.blocking_task;
      card.append(
        el(
          "p",
          uiText(
            "预览无时间限制；确认执行前会重新校验资料、MOC 关系、权限和整理规则。",
          ),
          "muted",
        ),
      );
      const expired = previewInvalidated(job);
      if (expired || job.application_blocked || job.blocking_task)
        accept.disabled = true;
      if (expired) {
        accept.disabled = true;
        card.append(
          el(
            "p",
            uiText(
              "资料、MOC 关系、权限或整理规则已变化，请按当前资料生成新预览。",
            ),
            "task-impact",
          ),
        );
      }
      if (expired) {
        actions.append(
          control("重新核对预览", action("recheck")),
          control("按当前资料生成新预览…", () => {
            preparation.open = true;
            const input =
              form.querySelector('[role="combobox"]') ||
              form.querySelector("select");
            input?.scrollIntoView({ block: "center", behavior: "smooth" });
            input?.focus({ preventScroll: true });
          }),
        );
      }
      accept.addEventListener("change", () => {
        draft.confirmations.set(confirmationKey, accept.checked);
        button.disabled =
          previewInvalidated(job) ||
          !!job.application_blocked ||
          !!job.blocking_task ||
          !accept.checked;
      });
      actions.append(consent, button);
    }
    appendTaskControls(actions, job, action);
    if (
      job.state === "completed" &&
      job.items.some((i) => !manuallyResolved(i) && i.analysis_error)
    ) {
      actions.append(control("复用缓存核对遗留项", action("recover_cache")));
      card.append(
        el(
          "p",
          uiText(
            "仅核对未解决项的已有回复，不发送模型请求。已完成分类保持；结果需确认后应用。",
          ),
          "muted",
        ),
      );
    }
    if (
      job.cache_recovery &&
      !job.cache_recovery.allow_missing &&
      !job.cache_recovery.retry_unknown?.length &&
      job.items.some((i) => !manuallyResolved(i))
    )
      card.append(
        el(
          "p",
          uiText(
            "本轮仅复用历史模型缓存；超时和缺少回复的条目保留，不自动重发。",
          ),
          "muted",
        ),
      );
    const ui = (draft.views[job.id] ||= {
      filter: taskEnded(job)
        ? "all"
        : stats.error
          ? "error"
          : stats.changed
            ? "changed"
            : planning
              ? "waiting"
              : stats.review
                ? "review"
                : stats.processed
                  ? "processed"
                  : "unchanged",
      page: 0,
      query: "",
      opened: [],
    });
    if (taskEnded(job) && !ui.historyView) {
      ui.filter = "all";
      ui.page = 0;
      ui.query = "";
      ui.historyView = true;
    }
    if (ui.filter !== "all" && !stats[ui.filter])
      ui.filter = stats.changed
        ? "changed"
        : stats.error
          ? "error"
          : stats.review
            ? "review"
            : stats.waiting
              ? "waiting"
              : stats.processed
                ? "processed"
                : "unchanged";
    const search = el("input", null, "task-search");
    setText(search, uiText("查找本次笔记标题或路径"), "placeholder");
    setText(search, uiText("查找本次整理条目"), "aria-label");
    search.value = ui.query;
    const filters = el("div", null, "task-filters"),
      list = el("div"),
      pages = el("div", null, "task-pages");
    function rows() {
      list.replaceChildren();
      pages.replaceChildren();
      const filtered = job.items.filter(
        (i) =>
          (ui.filter === "all" || itemCategory(i, job) === ui.filter) &&
          (!ui.query ||
            `${i.title} ${i.path}`
              .toLocaleLowerCase()
              .includes(ui.query.toLocaleLowerCase())),
      );
      ui.page = Math.min(
        ui.page,
        Math.max(0, Math.ceil(filtered.length / 25) - 1),
      );
      for (const item of filtered.slice(ui.page * 25, ui.page * 25 + 25)) {
        const category = itemCategory(item, job),
          resolved = manuallyResolved(item),
          verified = verifiedArchive(item, job),
          row = el("details", null, "task-row");
        row.dataset.viewKey = `maintenance:${job.id}:${item.entity}`;
        row.open = ui.opened.includes(item.entity);
        row.addEventListener("toggle", () => {
          ui.opened = ui.opened.filter((id) => id !== item.entity);
          if (row.open) ui.opened.push(item.entity);
        });
        const additions =
          !resolved && item.add?.length ? " → 新增 " + names(item.add) : "";
        const removals =
          !resolved && item.remove?.length
            ? "；移除 " + names(item.remove)
            : "";
        const state = verified
          ? "已核验归档（历史异常保留）"
          : resolved
            ? labels.processed
            : item.review_reason === "body_text_required"
              ? "待补充正文"
              : category === "error" || category === "review"
                ? labels[category]
                : item.state === "done"
                  ? "已处理"
                  : labels[category];
        const summary = el("summary", null, "task-row-summary");
        summary.append(
          el("strong", item.title),
          el("span", uiText(state), "status-badge"),
        );
        if (additions || removals)
          summary.append(
            el("small", uiText(additions + removals), "task-row-change"),
          );
        row.append(summary);
        appendRecordedPaths(row, summary, item, job);
        if (job.mode === "quick") inspectItem(row, item.path);
        appendItemStages(row, item, job);
        appendArchiveIssue(row, item);
        if (resolved) {
          row.append(
            el(
              "p",
              phrase("人工挂载：{p0}", {
                p0: names(item.manual_resolution.moc_ids),
              }),
            ),
            el(
              "p",
              uiText(
                "本项已通过人工挂载处理，不再参与本任务的异常重试或模型补充。",
              ),
              "muted",
            ),
          );
          const history = disclosure("历史诊断（已人工处理）");
          if (item.error || item.analysis_error)
            history.append(
              el(
                "p",
                uiText(
                  explain(
                    item.error || item.analysis_error,
                    item.analysis_detail || item.error_detail,
                  ),
                ),
                "muted",
              ),
            );
          showJSON(history, item, "原始分析与人工处理记录");
          row.append(history);
          list.append(row);
          continue;
        }
        row.append(
          el(
            "p",
            phrase("任务记录整理前归属：{p0}", { p0: names(item.before) }),
          ),
          el(
            "p",
            phrase(
              job.confirmed ? "任务记录目标归属：{p0}" : "计划整理后归属：{p0}",
              { p0: names(item.after) },
            ),
          ),
        );
        if (item.deferred_target)
          row.append(
            el(
              "p",
              phrase("归档延后：{p0}", { p0: item.deferred_target }),
              "muted",
            ),
          );
        if (item.review_reason === "body_text_required")
          row.append(
            el(
              "p",
              uiText(
                "正文没有足够的可读文字（例如纯图片 PPT）。资料保留，补充 OCR 文本或设置明确分类规则后再整理。",
              ),
              "muted",
            ),
          );
        if (item.review_reason === "no_matching_moc")
          row.append(
            el(
              "p",
              uiText(
                "已有回复未找到适合的 MOC，保留待复核。可补充分类规则或合适的 MOC 后再整理。",
              ),
              "muted",
            ),
          );
        if (item.error || item.analysis_error)
          row.append(
            el(
              "p",
              verified
                ? phrase("已核验归档；历史错误：{p0}。", {
                    p0: item.error || item.analysis_error,
                  })
                : job.state === "closed"
                  ? historicalItemFailure(item)
                  : item.analysis_error ===
                      "organization_model_evidence_invalid"
                    ? phrase(
                        "{p0}。本项原关系保留，其余项目继续分析；可在预览时重新核对异常项。",
                        {
                          p0:
                            evidenceFailure(item.analysis_detail) ||
                            "本项分类证据需核对",
                        },
                      )
                    : uiText(
                        explain(
                          item.error || item.analysis_error,
                          item.analysis_detail || item.error_detail,
                        ),
                      ),
              verified ? "muted" : "task-failure",
            ),
          );
        showJSON(row, item, "判断依据与技术详情");
        list.append(row);
      }
      if (!filtered.length)
        list.append(el("p", uiText("此筛选下暂无条目。"), "muted"));
      if (ui.page)
        pages.append(
          control("上一页", async () => {
            ui.page--;
            rows();
          }),
        );
      pages.append(
        el(
          "span",
          phrase("共 {p0} 项 · 第 {p1} / {p2} 页", {
            p0: filtered.length,
            p1: ui.page + 1,
            p2: Math.max(1, Math.ceil(filtered.length / 25)),
          }),
        ),
      );
      if ((ui.page + 1) * 25 < filtered.length)
        pages.append(
          control("下一页", async () => {
            ui.page++;
            rows();
          }),
        );
      for (const button of filters.children)
        button.setAttribute(
          "aria-pressed",
          String(button.dataset.filter === ui.filter),
        );
    }
    for (const key of [
      "changed",
      "review",
      "error",
      "waiting",
      "unchanged",
      "processed",
      "all",
    ]) {
      const count = key === "all" ? stats.total : stats[key],
        button = control(
          joinText(
            [
              uiText(
                taskEnded(job) && key === "changed" ? "记录变更" : labels[key],
              ),
              count,
            ],
            " ",
          ),
          async () => {
            ui.filter = key;
            ui.page = 0;
            rows();
          },
        );
      button.dataset.filter = key;
      filters.append(button);
    }
    search.addEventListener("input", () => {
      ui.query = search.value;
      ui.page = 0;
      rows();
    });
    card.append(filters, search, list, pages);
    rows();
    if (job.taxonomy?.create_candidates?.length)
      card.append(
        disclosure(
          "新 MOC 建议（在新预览中明确填写后确认）",
          ...job.taxonomy.create_candidates.map((p) =>
            el(
              "p",
              joinText([
                "",
                p.name || "主题建议",
                "：",
                p.reason || p.action || "待核对",
                "",
              ]),
            ),
          ),
        ),
      );
    showJSON(
      card,
      {
        id: job.id,
        state: job.state,
        stage: job.stage,
        error: job.error,
        error_detail: job.error_detail,
        recovery_error: job.recovery_error,
        index_last_result: job.index_state,
        index_observation: job.index_observation,
        last_progress_at: job.last_progress_at,
        read_work: job.read_work,
        plan_sha256: job.plan_sha256,
        created_at: job.created_at,
      },
      "任务诊断详情",
    );
    if (actions.children.length) {
      bindMaintenanceSubmission(actions, submission);
      card.append(actions);
    }
    return card;
  }
  if (!current) {
    const coverage = coverageNotice(
      result.coverage,
      "organization-coverage",
      false,
      { scope: fixed, api },
    );
    if (coverage) host.append(coverage);
  }
  const form = createMaintenancePreviewForm({
    fixed,
    api,
    refresh,
    draft,
    descriptions,
    catalogReady: readiness.catalogReady !== false,
    catalogError: readiness.catalogError,
  });
  preparation = mountMaintenanceWorkspace(host, {
    jobs,
    draft,
    form,
    renderJob,
  });
  appendInboxReviewHistory(host, { fixed, api, refresh, draft });
  return host;
}
