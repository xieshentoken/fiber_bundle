// Durable task facts describe a record, never the current filesystem location.
import { el } from "./ui.js";
import { joinText, localizedDate, phrase, uiText } from "./i18n/index.js";

export function taskEnded(job) {
  return ["completed", "cancelled", "closed"].includes(job.state);
}

function recordedTime(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? localizedDate(value * 1000, { dateStyle: "medium", timeStyle: "short" })
    : null;
}

export function taskRecordSummary(job) {
  const created = recordedTime(job.created_at),
    closed = recordedTime(job.closed_at),
    completed = recordedTime(job.completed_at),
    updated = recordedTime(job.updated_at);
  const parts = [
    phrase("任务：{p0}", { p0: job.id }),
    created ? phrase("生成：{p0}", { p0: created }) : uiText("生成时间未记录"),
  ];
  if (closed) parts.push(phrase("结束：{p0}", { p0: closed }));
  else if (completed) parts.push(phrase("完成：{p0}", { p0: completed }));
  else if (updated) parts.push(phrase("更新：{p0}", { p0: updated }));
  return joinText(parts, " · ");
}

export function appendTaskRecord(host, job) {
  host.append(el("p", taskRecordSummary(job), "muted"));
  if (taskEnded(job))
    host.append(
      el(
        "p",
        uiText(
          "这是已结束任务的历史记录；所列来源与归档位置不是当前文件位置检查。",
        ),
        "task-impact",
      ),
    );
}

export function appendRecordedPaths(host, summary, item, job) {
  host.append(
    el("p", phrase("任务记录来源：{p0}", { p0: item.path }), "muted"),
  );
  const target = item.target || item.deferred_target;
  if (!target) return;
  const archived =
    item.phase === "archived" &&
    job.state !== "preview" &&
    !job.rollback_completed;
  const text = phrase(
    archived ? "任务记录归档位置：{p0}" : "计划归档目标：{p0}",
    { p0: target },
  );
  host.append(el("p", text));
  summary.append(el("small", text, "task-row-destination"));
}
