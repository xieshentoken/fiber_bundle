// Task records and preview-time candidate selection use explicit historical labels.
export default {
  "visibility.current": ["当前任务", "Current task"],
  "visibility.empty_heading": [
    "候选检查 · 无可执行条目",
    "Candidate check · No executable items",
  ],
  "visibility.no_confirmation": ["无需确认", "No confirmation required"],
  "visibility.other_running": [
    "其他任务仍标为执行中：{p0}。请展开对应记录核对。",
    "Other tasks are still marked as executing: {p0}. Expand their records to review them.",
  ],
  "visibility.task": ["任务：{p0}", "Task: {p0}"],
  "visibility.created": ["生成：{p0}", "Created: {p0}"],
  "visibility.closed": ["结束：{p0}", "Ended: {p0}"],
  "visibility.completed": ["完成：{p0}", "Completed: {p0}"],
  "visibility.updated": ["更新：{p0}", "Updated: {p0}"],
  "visibility.created_unknown": [
    "生成时间未记录",
    "Creation time was not recorded",
  ],
  "visibility.record": [
    "这是已结束任务的历史记录；所列来源与归档位置不是当前文件位置检查。",
    "This is a historical record of an ended task. Its source and archive paths are not a check of current file locations.",
  ],
  "visibility.source": ["任务记录来源：{p0}", "Recorded task source: {p0}"],
  "visibility.archived": [
    "任务记录归档位置：{p0}",
    "Recorded archive location: {p0}",
  ],
  "visibility.planned_target": [
    "计划归档目标：{p0}",
    "Planned archive target: {p0}",
  ],
  "visibility.before": [
    "任务记录整理前归属：{p0}",
    "Recorded memberships before organization: {p0}",
  ],
  "visibility.after": [
    "任务记录目标归属：{p0}",
    "Recorded target memberships: {p0}",
  ],
  "visibility.planned_after": [
    "计划整理后归属：{p0}",
    "Planned memberships after organization: {p0}",
  ],
  "visibility.changes": ["记录变更", "Recorded changes"],
  "visibility.progress": ["历史处理记录", "Historical processing record"],
  "visibility.progress_ended": [
    "历史处理记录：{p0} / {p1} 项；任务已结束。",
    "Historical processing record: {p0} / {p1} items; the task has ended.",
  ],
  "visibility.unknown": ["未记录", "Not recorded"],
  "visibility.candidates_legacy": [
    "此任务未记录 Inbox 发现与跳过统计，无法据此判断是否覆盖全部 Inbox。",
    "This task did not record Inbox discovery and skip counts, so its records cannot establish full Inbox coverage.",
  ],
  "visibility.candidates": [
    "预览时 Inbox 候选（按笔记或专家包计）：发现 {p0} · 纳入 {p1} · 跳过 {p2}",
    "Inbox candidates at preview time (notes or expert packages): Found {p0} · Included {p1} · Skipped {p2}",
  ],
  "visibility.empty": [
    "本次没有可执行条目。请按跳过原因核对资料，再生成新预览；确认或恢复此任务不会纳入跳过的资料。",
    "This preview has no executable items. Review any skip reasons, then create a new preview. Confirming or resuming this task will not include skipped items.",
  ],
  "visibility.moc_only": [
    "本次未纳入笔记；确认只会创建本预览列出的新 MOC，不会处理跳过的资料。",
    "No notes were included. Confirmation will only create the new MOCs listed in this preview; skipped items will not be processed.",
  ],
  "visibility.skipped": [
    "查看未纳入本次整理的资料",
    "View items excluded from this task",
  ],
  "visibility.unpublished": [
    "导入尚未完成发布，未纳入本次整理；请先完成该导入，再生成新预览。",
    "The import has not completed publication and was excluded. Complete that import, then create a new preview.",
  ],
  "visibility.skipped_unknown": [
    "未纳入本次候选，请核对资料状态。",
    "Excluded from these candidates. Review the item's status.",
  ],
  "visibility.package_unregistered": [
    "专家包尚未登记，未纳入本次整理；请先核对该专家包的导入状态。",
    "The expert package is unregistered and was excluded. Review its import status first.",
  ],
  "visibility.symlink": [
    "符号链接不参与整理；请核对原文件及允许访问的目录。",
    "Symbolic links are excluded. Review the source file and permitted directories.",
  ],
  "visibility.hidden": ["隐藏文件不参与整理。", "Hidden files are excluded."],
  "visibility.incomplete": [
    "候选统计尚未确认完整；所列计数不代表已覆盖全部 Inbox。",
    "Candidate statistics are not confirmed complete. These counts do not establish full Inbox coverage.",
  ],
  "visibility.partial_skipped": [
    "这里只列出部分已记录的跳过资料。",
    "Only some recorded skipped items are listed here.",
  ],
  "visibility.skipped_truncated": [
    "这里只列出部分跳过资料；统计包含全部跳过项。",
    "Only some skipped items are listed here; the counts include all skipped items.",
  ],
};
