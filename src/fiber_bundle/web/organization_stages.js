// Membership and archive completion are independent; a terminal task can retain review items.
import { el } from "./ui.js";
import { phrase, uiText } from "./i18n/index.js";

export function itemStages(item, job) {
  const mounted = item.membership_observation?.state === "current";
  const unverified =
    !mounted && ["members_done", "archived"].includes(item.phase) && item.after?.length;
  return {
    classification: item.analysis_error
      ? "待核对"
      : item.evaluated
        ? "已分析"
        : "等待分析",
    membership:
      mounted
        ? "已挂载"
        : unverified
          ? "挂载待核对"
          : item.status === "review"
          ? "待复核"
          : "尚未挂载",
    archive:
      item.phase === "archived"
        ? "已归档"
        : item.deferred_target
          ? "归档延后"
          : item.target
            ? "等待归档"
            : job.mode !== "quick"
              ? "本模式不移动文件"
              : "未计划归档",
  };
}

export function appendItemStages(host, item, job) {
  const value = itemStages(item, job);
  host.append(
    el(
      "p",
      phrase("分类：{p0} · MOC：{p1} · 归档：{p2}", {
        p0: uiText(value.classification),
        p1: uiText(value.membership),
        p2: uiText(value.archive),
      }),
      "muted",
    ),
  );
  if (item.wait)
    host.append(
      el(
        "p",
        uiText(
          item.wait.reason === "changed"
            ? "当前正文已变化，请保留已完成结果并按当前资料生成新预览。"
            : "此项等待文件可读，其他无冲突条目可继续。",
        ),
        "task-impact",
      ),
    );
}

export function stageTotals(job) {
  const items = job.items || [];
  return {
    analyzed: items.filter((i) => i.evaluated).length,
    mounted: items.filter((i) => itemStages(i, job).membership === "已挂载")
      .length,
    archived: items.filter((i) => i.phase === "archived").length,
    waiting: items.filter((i) =>
      ["waiting_file", "waiting_archive"].includes(i.state),
    ).length,
  };
}

export function appendStageSummary(host, job) {
  if (!job.confirmed) return;
  const totals = stageTotals(job);
  host.append(
    el(
      "p",
      phrase("已分析 {p0} · 当前已挂载 {p1} · 已归档 {p2} · 等待 {p3}", {
        p0: totals.analyzed,
        p1: totals.mounted,
        p2: totals.archived,
        p3: totals.waiting,
      }),
      "task-stage-summary",
    ),
  );
  if (job.mode !== "quick")
    host.append(
      el("p", uiText("本模式只调整 MOC 归属，不移动文件。"), "muted"),
    );
  else if (job.archive_mode === "off")
    host.append(
      el(
        "p",
        uiText(
          "此任务按已确认的快速整理预览归档；保存规则中的自动挂载后归档开关未启用。",
        ),
        "muted",
      ),
    );
}
