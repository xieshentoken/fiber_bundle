import { locale } from "./i18n/index.js";
const labels = {
  files: ["个文件", "files"],
  previewPhase: ["等待确认", "Awaiting confirmation"],
  title: ["导入专家 Draft 包", "Import expert Draft package"],
  close: ["关闭", "Close"],
  help: [
    "选择完整 Draft 文件夹。校验后复制到 Book，保留源包；不会重新提取或调用模型。",
    "Select a complete Draft folder. Validate and copy to Book while keeping the source. No extraction or model calls.",
  ],
  identity: ["操作身份", "Identity"],
  vault: ["目标知识库", "Destination vault"],
  source: ["Draft 文件夹", "Draft folder"],
  choose: ["选择 Draft 文件夹", "Choose Draft folder"],
  folder: [
    "Book 内文件夹名（留空沿用源目录名）",
    "Folder name in Book (empty keeps the source name)",
  ],
  preview: ["校验并生成预览", "Validate and preview"],
  history: ["导入任务", "Import tasks"],
  refresh: ["刷新任务", "Refresh tasks"],
  confirm: ["确认导入并挂载", "Confirm import and MOC links"],
  acknowledge: [
    "我确认这是未独立核验的 Draft；所选 MOC 将人工增挂并锁定。",
    "I acknowledge this unverified Draft. Selected MOC links will be added and locked as manual choices.",
  ],
  none: [
    "可不选 MOC，仅导入后按现有策略处理。",
    "Leave MOCs unselected to import and follow the existing policy.",
  ],
  mocs: ["选择 MOC（可多选）", "Choose MOCs (multiple)"],
  search: ["筛选 MOC", "Filter MOCs"],
  cancel: ["停止后续步骤", "Stop remaining steps"],
  resume: ["恢复未完成步骤", "Resume unfinished steps"],
  interrupted: [
    "操作未确认，请刷新任务核对；不要重复提交。",
    "Result unconfirmed. Refresh tasks before submitting again.",
  ],
  loading: ["正在读取…", "Loading…"],
  noscope: [
    "请选择具有查询与新增权限的身份和可写知识库。",
    "Select an identity with query/add permissions and a writable vault.",
  ],
  sourceMissing: ["请先选择 Draft 文件夹。", "Choose a Draft folder first."],
  selected: ["已选", "Selected"],
  target: ["目标", "Destination"],
  reuse: [
    "同版本已存在，将复用整包，不重复复制。",
    "This version already exists. Reuse it without copying again.",
  ],
  complete: [
    "整包已导入，所选 MOC 已挂载，索引已更新。",
    "Package imported, selected MOCs linked, index updated.",
  ],
  partial: [
    "整包已保留，部分 MOC 未完成；恢复只补未完成项。",
    "Package retained. Some MOC links are unfinished; resume only those steps.",
  ],
  stopped: [
    "已停止后续步骤；已完成的导入或挂载保留。",
    "Remaining steps stopped. Completed import or MOC links are retained.",
  ],
  ignored: [
    "系统 .DS_Store 不属于 Draft，不复制。",
    "System .DS_Store is not part of the Draft and is excluded.",
  ],
  validation: ["校验源包", "Validate source"],
  copy: ["复制文件", "Copy files"],
  verification: ["校验目标包", "Verify destination"],
  registration: ["登记整包", "Register package"],
  membership: ["挂载 MOC", "Link MOCs"],
  index: ["更新索引", "Update index"],
  queued: ["已排队", "Queued"],
  working: ["处理中", "Working"],
  completed: ["全部完成", "Completed"],
  previewState: ["预览待确认", "Preview ready"],
  failed: ["未完成", "Failed"],
  cancelled: ["已停止", "Stopped"],
  interruptedState: ["已中断，可恢复", "Interrupted, resumable"],
  empty: ["当前范围暂无导入任务。", "No import tasks in this scope."],
};
const errors = {
  draft_book_rules_not_ready: [
    "请先在知识库保存规则中配置并启用统一目录，确认 Book 地址。",
    "Configure and enable unified save rules and the Book directory first.",
  ],
  draft_destination_exists: [
    "目标目录已存在。请修改文件夹名后重新校验；不会覆盖原目录。",
    "Destination exists. Change the folder name and validate again; existing files are protected.",
  ],
  draft_package_unlisted_files: [
    "源目录有未列入清单的文件。请选择实际 Draft 根目录，或由提取工具检查完整包。",
    "Unlisted files were found. Select the actual Draft root or check the package with its extraction tool.",
  ],
  draft_source_changed: [
    "源包在校验后发生变化。请保持源包稳定后重新生成预览。",
    "Source changed after validation. Keep it stable and create a new preview.",
  ],
  draft_insufficient_space: [
    "Book 所在磁盘空间不足；请释放空间后重试。",
    "Not enough space on the Book volume. Free space and retry.",
  ],
  draft_version_upgrade_requires_review: [
    "该书已有另一版本。此次不覆盖旧版本，请先核对版本差异。",
    "Another version of this book exists. Review the differences first; it will not be overwritten.",
  ],
  expert_package_protocol_incompatible: [
    "不是当前支持的完整 fake-expert Draft 协议。仅全文或 workpack 不能直接导入。",
    "Not a supported complete fake-expert Draft. Extracted text or a workpack alone cannot be imported.",
  ],
  draft_target_changed: [
    "知识库位置、权限或保存规则已变化，请重新预览。",
    "Vault location, permissions or save rules changed. Preview again.",
  ],
  draft_backend_restarted: [
    "后台已重启。确认后恢复未完成步骤。",
    "Backend restarted. Resume unfinished steps explicitly.",
  ],
};
export function draftText(key) {
  return labels[key]?.[locale() === "en" ? 1 : 0] || key;
}
export function draftError(code) {
  return errors[code]?.[locale() === "en" ? 1 : 0] || code;
}
export function taskSummary(task) {
  if (task.state === "completed") return draftText("complete");
  if (task.state === "partial") return draftText("partial");
  if (task.state === "cancelled") return draftText("stopped");
  const state =
    task.state === "preview"
      ? "previewState"
      : task.state === "interrupted"
        ? "interruptedState"
        : task.state;
  return [
    draftText(state),
    draftText(task.phase === "preview" ? "previewPhase" : task.phase),
    task.error ? draftError(task.error) : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
