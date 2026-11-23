export default {
  "organization.recovery.retained": [
    "部分历史成员的原文件已不在登记路径。已保留原 MOC 关系与“当前不可见”标记；这些成员仍需核对，不影响已验证的新挂载。",
    "Some historical members are missing at their registered paths. Their MOC memberships and existing unavailable markers are preserved. They still need review; verified new memberships can proceed.",
  ],
  "organization.recovery.catalog": [
    "当前目录清单尚未通过核对。生成预览会先检查所选模式的范围；预检失败时不会创建整理任务。请先点击“更新此知识库索引”核对复制或编辑后的内容。",
    "The current catalog has not passed validation. Preview first checks the selected mode's scope; failed preflight creates no task. Update this vault's index to reconcile copied or edited content.",
  ],
  "organization.recovery.not_created": [
    "预检未通过，整理任务尚未创建。输入已保留；更新此知识库索引完成后可再次生成预览。",
    "Preflight did not pass; no organization task was created. Your input is kept. Update this vault's index, then generate the preview again.",
  ],
  "organization.recovery.directory": ["需核对的目录", "Directory to reconcile"],
  "organization.recovery.root": ["知识库根目录", "Vault root"],
  "organization.recovery.metadata": [
    "目录观察与当前文件状态不一致。更新索引将重新核对，不会移动或覆盖笔记。",
    "The directory observation differs from its current state. Updating the index reconciles it without moving or overwriting notes.",
  ],
  "organization.recovery.names": [
    "目录内的名称发生变化，需要更新索引后重新核对。",
    "Directory entries changed. Update the index to reconcile them.",
  ],
  "organization.recovery.changing": [
    "目录在本次核对期间仍有变化，请在复制或保存完成后更新索引。",
    "The directory is still changing. Update the index after copying or saving finishes.",
  ],
  "organization.recovery.pending": [
    "后台尚未完成所选范围的目录核对，请在全局索引状态中查看进度。",
    "Background directory reconciliation is unfinished. Check the global index status for progress.",
  ],
  "organization.recovery.cached_resume": [
    "复用缓存恢复挂载（不调用模型）",
    "Resume mounting from cache (no model calls)",
  ],
  "organization.recovery.cached_intro": [
    "已有分析结果已保存。恢复将重新核对当前资料、权限与 MOC，仅补未完成挂载；无法复用时停止，不重新调用模型。",
    "Analysis results are saved. Recovery rechecks current sources, access and MOCs, then completes unfinished memberships. It stops if reuse is unsafe, without new model calls.",
  ],
};
