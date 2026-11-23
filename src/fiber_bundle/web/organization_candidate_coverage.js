// Candidate selection is distinct from read availability and does not authorize recovery.
import { el } from "./ui.js";
import { disclosure } from "./view_sections.js";
import { joinText, phrase, sourceText, uiText } from "./i18n/index.js";
import { unavailableReason } from "./organization_coverage.js";
import { requestErrors } from "./request_errors.js";

function skipReason(code) {
  const reasons = {
    publication_not_committed:
      "导入尚未完成发布，未纳入本次整理；请先完成该导入，再生成新预览。",
    expert_package_not_registered:
      "专家包尚未登记，未纳入本次整理；请先核对该专家包的导入状态。",
    symbolic_link: "符号链接不参与整理；请核对原文件及允许访问的目录。",
    hidden_file: "隐藏文件不参与整理。",
  };
  const local = Object.hasOwn(reasons, code) ? reasons[code] : null,
    unavailable = unavailableReason(code),
    errorCode =
      code === "vault_sync_cloud_metadata_unavailable"
        ? "cloud_metadata_unavailable"
        : code;
  const known = Object.hasOwn(requestErrors, errorCode)
    ? requestErrors[errorCode]
    : null;
  if (local || unavailable || known)
    return uiText(local || unavailable || known[0]);
  return joinText(
    [uiText("未纳入本次候选，请核对资料状态。"), sourceText(code)],
    " · ",
  );
}

function count(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : uiText("未记录");
}

export function emptyCandidatePreview(job) {
  return (
    job.state === "preview" &&
    !job.items?.length &&
    !job.new_mocs?.length &&
    job.candidate_coverage?.included_count === 0
  );
}

export function appendCandidateCoverage(host, job, inspectItem) {
  if (job.mode !== "quick") return;
  const coverage = job.candidate_coverage,
    notice = el("div", null, "task-impact");
  notice.dataset.candidateCoverage = job.id;
  if (!coverage) {
    notice.append(
      el(
        "p",
        uiText(
          "此任务未记录 Inbox 发现与跳过统计，无法据此判断是否覆盖全部 Inbox。",
        ),
      ),
    );
    host.append(notice);
    return;
  }
  notice.append(
    el(
      "p",
      phrase(
        "预览时 Inbox 候选（按笔记或专家包计）：发现 {p0} · 纳入 {p1} · 跳过 {p2}",
        {
          p0: count(coverage.discovered_count),
          p1: count(coverage.included_count),
          p2: count(coverage.skipped_count),
        },
      ),
    ),
  );
  if (coverage.complete !== true)
    notice.append(
      el(
        "p",
        uiText("候选统计尚未确认完整；所列计数不代表已覆盖全部 Inbox。"),
        "muted",
      ),
    );
  if (emptyCandidatePreview(job))
    notice.append(
      el(
        "p",
        uiText(
          "本次没有可执行条目。请按跳过原因核对资料，再生成新预览；确认或恢复此任务不会纳入跳过的资料。",
        ),
      ),
    );
  else if (
    !job.items?.length &&
    coverage.included_count === 0 &&
    job.new_mocs?.length
  )
    notice.append(
      el(
        "p",
        uiText(
          "本次未纳入笔记；确认只会创建本预览列出的新 MOC，不会处理跳过的资料。",
        ),
      ),
    );
  if (coverage.skipped_count > 0 || coverage.skipped?.length) {
    const details = disclosure("查看未纳入本次整理的资料");
    details.dataset.viewKey = `candidate-coverage:${job.id}`;
    for (const item of coverage.skipped || []) {
      const reason = skipReason(item.reason);
      const row = el("div");
      row.append(el("p", joinText([sourceText(item.path), reason], " · ")));
      inspectItem?.(row, item.path);
      details.append(row);
    }
    if (coverage.skipped_truncated)
      details.append(
        el(
          "p",
          uiText(
            coverage.complete === true
              ? "这里只列出部分跳过资料；统计包含全部跳过项。"
              : "这里只列出部分已记录的跳过资料。",
          ),
          "muted",
        ),
      );
    notice.append(details);
  }
  host.append(notice);
}
