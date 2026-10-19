// Chinese labels and tones for backend state enums; unknown values stay visible verbatim.
const runs = {
  queued: ["已入队", "waiting"],
  running: ["执行中", "waiting"],
  waiting_budget: ["等待预算", "waiting"],
  succeeded: ["执行完成", "success"],
  failed: ["执行失败", "error"],
  cancelled: ["已取消", "info"],
  interrupted: ["执行中断", "error"],
};
const documents = {
  queued: ["待提取", "waiting"],
  extracting: ["提取中", "waiting"],
  extracted: ["已提取，待审核", "waiting"],
  failed: ["提取失败", "error"],
  interrupted: ["提取中断", "error"],
};
const media = {
  queued: ["待处理", "waiting"],
  running: ["处理中", "waiting"],
  complete: ["处理完成，待审核", "waiting"],
  failed: ["处理失败", "error"],
  cancelled: ["已取消", "info"],
  interrupted: ["处理中断", "error"],
};
const publications = {
  prepared: ["发布已准备", "waiting"],
  publishing: ["发布中", "waiting"],
  published: ["已发布", "success"],
  conflict: ["发布冲突", "error"],
  not_published: ["未发布", "info"],
};
function label(table, value) {
  return table[value] || [value || "状态未记录", "info"];
}
export const runState = (value) => label(runs, value);
export const documentState = (value) => label(documents, value);
export const mediaState = (value) => label(media, value);
export const publicationState = (value) => label(publications, value);
// Short identifier for display; the full value stays in the title attribute.
export function shortId(value) {
  return String(value || "").slice(0, 8);
}
const bindings = {
  document: "文档提取",
  source: "来源处理",
  notes: "笔记查询",
};
export function bindingLabel(value) {
  return bindings[value] || (value ? "执行任务 · " + value : "执行任务");
}
const calls = {
  reserved: ["已预留", "waiting"],
  settled: ["已结算", "success"],
  uncertain: ["结果未知", "error"],
};
export const callState = (value) => label(calls, value);
