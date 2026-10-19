// Read-only projections shared by the home and inbox; never starts a task.
import { pendingItems } from "./home_status.js";
import { automaticStatus } from "./automatic_status.js";
const sources = {
  documents: ["documents", "文档"],
  articles: ["documents", "文章"],
  media: ["media", "媒体"],
  videos: ["media", "视频提取"],
  imports: ["imports", "导入"],
  automatic: ["vaults", "自动处理"],
  executions: ["execution", "执行任务"],
  batches: ["batches", "收件批次"],
};
const busy = new Set([
  "queued",
  "running",
  "claimed",
  "waiting",
  "processing",
  "extracting",
  "publishing",
  "collecting",
]);
const done = new Set([
  "published",
  "complete",
  "completed",
  "succeeded",
  "cancelled",
  "superseded",
]);
export function activityCoverage(records) {
  const sources = [
    "vaults",
    "batches",
    "documents",
    "media",
    "articles",
    "imports",
    "expert",
    "automatic",
    "videos",
    "channels",
    "executions",
  ];
  return sources.every((name) => records[name] && !records[name].error);
}
export function activityRows(records) {
  const pending = new Set(
    pendingItems(records).map((i) => `${i.source}:${i.id}`),
  );
  const result = { processing: [], history: [] };
  for (const [source, [page, label]] of Object.entries(sources)) {
    const record = records[source],
      raw = record?.value;
    const items = Array.isArray(raw)
      ? raw
      : raw?.jobs || raw?.tasks || raw?.runs || [];
    for (const item of items) {
      const id = item.id || item.run_id || item.task_id || item.job_id;
      const keySource = source === "batches" ? "batch" : source;
      if (!id || pending.has(`${keySource}:${id}`)) continue;
      const automatic = source === "automatic" ? automaticStatus(item) : null;
      if (automatic?.actionRequired) continue;
      const state =
        automatic?.state ||
        item.expert?.state ||
        item.automatic_state ||
        item.automatic?.state ||
        item.state ||
        item.status;
      const published =
        item.publication?.state === "published" ||
        item.publication_state === "published" ||
        state === "published";
      const processing = !published && busy.has(state);
      const finished = published || done.has(state);
      if (!processing && !finished) continue;
      const status = published
        ? "已保存"
        : processing
          ? {
              queued: "排队中",
              collecting: "收件缓冲中",
              publishing: "保存中",
            }[state] || "处理中"
          : {
              cancelled: "已取消",
              superseded: "已由后续任务接替",
              succeeded: "执行完成",
            }[state] ||
            automatic?.text ||
            "处理完成 · 保存状态见详情";
      result[processing ? "processing" : "history"].push({
        source: keySource,
        id,
        key: `${source}:${id}`,
        title:
          item.title || item.filename || item.preview?.split("\n")[0] || label,
        detail: label,
        page,
        client_id: item.client_id,
        vault_id: item.vault_id,
        status: record.error ? "上次记录 · 需刷新" : status,
        tone: record.error
          ? "error"
          : processing
            ? "info"
            : published
              ? "success"
              : "info",
      });
    }
  }
  return result;
}
