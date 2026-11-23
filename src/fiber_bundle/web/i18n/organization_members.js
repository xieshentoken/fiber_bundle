export default {
  "manual_members.unresolved": [
    "尚待核对的人工关系操作",
    "Manual membership operation awaiting review",
  ],
  "manual_members.recent": [
    "最近人工关系操作",
    "Latest manual membership operation",
  ],
  "manual_members.pending": [
    "正在同步冻结的所选 MOC，请等待结果；请勿重复提交。",
    "Syncing the submitted MOC selection. Wait for the result without submitting again.",
  ],
  "manual_members.completed": [
    "后台已确认全部所选关系调整完成。",
    "The backend confirmed all selected membership changes are complete.",
  ],
  "manual_members.partial": [
    "部分完成；已完成项保留，其余项需核对。",
    "Partially complete. Completed changes are retained; review the remaining items.",
  ],
  "manual_members.incomplete": [
    "所选关系调整尚未完成，请按逐项提示核对。",
    "The selected membership changes are incomplete. Review each item below.",
  ],
  "manual_members.unknown": [
    "结果未知；未收到完整的后台完成确认，不会自动重试。",
    "Result unknown. Complete backend confirmation was not received. This operation will not retry automatically.",
  ],
  "manual_members.item_pending": ["等待后台结果", "Waiting for backend result"],
  "manual_members.item_completed": ["已完成", "Completed"],
  "manual_members.item_incomplete": [
    "未完成，需核对",
    "Incomplete; review required",
  ],
  "manual_members.item_unknown": ["结果未知", "Result unknown"],
  "manual_members.clear_selection": ["清空本次选择", "Clear this selection"],
  "manual_members.unknown_help": [
    "刷新只核对当前挂载，不能证明 Map 写回或人工限制解除已完成。请核对所选 MOC 和原操作；确认后如需再次操作，先清空本次选择，再重新选择。",
    "Refreshing checks current memberships only; it cannot prove Map writeback or removal of manual restrictions is complete. Review the selected MOCs and original operation. If another operation is needed after review, clear this selection and select targets again.",
  ],
  "manual_members.timeout": [
    "等待后台结果超时，操作可能仍在执行。请先读取状态并核对，不要立即重复提交。",
    "Waiting for the backend timed out; the operation may still be running. Read the current state and review it before submitting again.",
  ],
  "manual_members.network": [
    "连接中断，无法确认操作结果。请先读取状态并核对，不要立即重复提交。",
    "The connection was interrupted and the operation result is unconfirmed. Read the current state and review it before submitting again.",
  ],
  "manual_members.invalid_response": [
    "后台未返回与本次所选 MOC 一一对应的有效结果，请先核对原操作。",
    "The backend did not return a valid result for every selected MOC. Review the original operation first.",
  ],
  "manual_members.catalog_pending": [
    "目录仍在观察中，请等待目录就绪后刷新核对；此提示不能作为本次关系调整已完成的证明。",
    "The catalog is still being observed. Wait until it is ready, then refresh to review. This message does not confirm the membership changes completed.",
  ],
  "manual_members.catalog_changed": [
    "目录在读取期间变化，请刷新核对当前目录与原操作。",
    "The catalog changed during reading. Refresh to review the current catalog and original operation.",
  ],
  "manual_members.catalog_unavailable": [
    "MOC 目录暂不可读，请刷新核对目录；保留原操作结果。",
    "The MOC catalog is temporarily unavailable. Refresh to review the catalog; retain the original operation result.",
  ],
  "manual_members.not_visible": [
    "笔记或 MOC 已失效，或不在当前权限范围内，请刷新核对权限和目标。",
    "The note or MOC is no longer valid or accessible in this scope. Refresh to review permissions and targets.",
  ],
  "manual_members.incomplete_help": [
    "关系同步未确认完成，可能已有部分写入。请核对该 MOC 与原操作，不要直接重复提交。",
    "Membership synchronization is not confirmed complete and some writes may already exist. Review this MOC and the original operation before submitting again.",
  ],
};
