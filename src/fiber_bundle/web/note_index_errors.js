// Index conflicts describe source state, rather than message-event deduplication.
export function noteIndexErrorText(code) {
  return {
    layout_tracked_note_replaced:
      "普通笔记或 Map 的文件身份已变化。请到知识库的“整理与布局迁移 → 批量核对外部改动”中核对位置并保留原身份，再更新索引；无需重新入库。",
    layout_rebind_required:
      "部分已发布笔记已移动、改名、替换或缺失。请到知识库的“整理与布局迁移 → 批量核对外部改动”中核对位置后，再更新索引。",
    note_index_update_required: "笔记索引已过期，请更新此知识库索引。",
    placement_reconciliation_required:
      "存在尚未完成的位置调整，请先在知识库整理面板恢复该操作，再更新索引。",
  }[code];
}
