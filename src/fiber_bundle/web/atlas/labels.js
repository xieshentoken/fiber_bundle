// Bilingual labels and tones for backend state enums. Unknown values stay visible verbatim.
import { strings } from "./i18n.js";

const t = strings({
  zh: {
    run_queued: "已入队",
    run_running: "执行中",
    run_waiting_budget: "等待预算",
    run_succeeded: "执行完成",
    run_failed: "执行失败",
    run_cancelled: "已取消",
    run_interrupted: "执行中断",
    doc_queued: "待提取",
    doc_extracting: "提取中",
    doc_extracted: "已提取，待审核",
    doc_failed: "提取失败",
    doc_interrupted: "提取中断",
    media_queued: "待处理",
    media_running: "处理中",
    media_complete: "处理完成，待审核",
    media_failed: "处理失败",
    media_cancelled: "已取消",
    media_interrupted: "处理中断",
    pub_prepared: "发布已准备",
    pub_publishing: "发布中",
    pub_published: "已发布",
    pub_conflict: "发布冲突",
    pub_not_published: "未发布",
    call_reserved: "已预留",
    call_settled: "已结算",
    call_uncertain: "结果未知",
    art_captured: "已抓取，待审核",
    art_published: "已发布",
    unknown: "状态未记录",
    verdict_uncertain: "待核对",
    verdict_reject: "不接受",
    verdict_accept: "已核对，接受",
    bind_document: "文档提取",
    bind_source: "来源处理",
    bind_notes: "笔记查询",
    bind_other: "执行任务 · {v}",
    bind_none: "执行任务",
  },
  en: {
    run_queued: "Queued",
    run_running: "Running",
    run_waiting_budget: "Waiting for budget",
    run_succeeded: "Succeeded",
    run_failed: "Failed",
    run_cancelled: "Cancelled",
    run_interrupted: "Interrupted",
    doc_queued: "Waiting to extract",
    doc_extracting: "Extracting",
    doc_extracted: "Extracted, needs review",
    doc_failed: "Extraction failed",
    doc_interrupted: "Extraction interrupted",
    media_queued: "Queued",
    media_running: "Processing",
    media_complete: "Processed, needs review",
    media_failed: "Processing failed",
    media_cancelled: "Cancelled",
    media_interrupted: "Interrupted",
    pub_prepared: "Publication prepared",
    pub_publishing: "Publishing",
    pub_published: "Published",
    pub_conflict: "Publication conflict",
    pub_not_published: "Not published",
    call_reserved: "Reserved",
    call_settled: "Settled",
    call_uncertain: "Outcome unknown",
    art_captured: "Captured, needs review",
    art_published: "Published",
    unknown: "State not recorded",
    verdict_uncertain: "Needs checking",
    verdict_reject: "Reject",
    verdict_accept: "Checked, accept",
    bind_document: "Document extraction",
    bind_source: "Source processing",
    bind_notes: "Note query",
    bind_other: "Run · {v}",
    bind_none: "Run",
  },
});
const tones = {
  run: {
    queued: "waiting",
    running: "waiting",
    waiting_budget: "waiting",
    succeeded: "success",
    failed: "error",
    cancelled: "info",
    interrupted: "error",
  },
  doc: {
    queued: "waiting",
    extracting: "waiting",
    extracted: "waiting",
    failed: "error",
    interrupted: "error",
  },
  media: {
    queued: "waiting",
    running: "waiting",
    complete: "waiting",
    failed: "error",
    cancelled: "info",
    interrupted: "error",
  },
  pub: {
    prepared: "waiting",
    publishing: "waiting",
    published: "success",
    conflict: "error",
    not_published: "info",
  },
  call: { reserved: "waiting", settled: "success", uncertain: "error" },
  art: { captured: "waiting", published: "success" },
};
function label(prefix, value) {
  if (!tones[prefix][value]) return [value || t("unknown"), "info"];
  return [t(`${prefix}_${value}`), tones[prefix][value]];
}
export const runState = (value) => label("run", value);
export const documentState = (value) => label("doc", value);
export const mediaState = (value) => label("media", value);
export const publicationState = (value) => label("pub", value);
export const callState = (value) => label("call", value);
export const articleState = (value) => label("art", value);
export const verdicts = () =>
  ["uncertain", "reject", "accept"].map((value) => [
    value,
    t("verdict_" + value),
  ]);
export const verdictText = (value) => t("verdict_" + value);
export function bindingLabel(value) {
  if (["document", "source", "notes"].includes(value))
    return t("bind_" + value);
  return value ? t("bind_other", { v: value }) : t("bind_none");
}
