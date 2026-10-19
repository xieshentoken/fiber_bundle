// Receipt intake is independent of note publication and indexing.
const receiptStates = {
  stored: "原件已收到",
  archived: "原件已归档",
  queued: "等待处理",
  running: "处理中",
  failed: "收件失败",
  cancelled: "已取消",
};
export function receiptSummary(receipt) {
  const id = receipt.job_id || receipt.id || "";
  return {
    title:
      receipt.title ||
      receipt.filename ||
      `收件 · ${id.slice(0, 10) || "编号未记录"}`,
    state: receiptStates[receipt.state] || receipt.state || "状态未知",
    detail: receipt.owner_client_id
      ? `来源身份：${receipt.owner_client_id}`
      : "来源身份未记录",
    publication: "笔记保存与索引状态请在收件箱核对。",
    tone: receipt.error_code ? "error" : "muted",
  };
}
