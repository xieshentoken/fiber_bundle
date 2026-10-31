// Shared bounded HTTP errors; no server exception text or operation replay policy in views.
export const requestErrors = {
  maintenance_membership_plan_changed: [
    "原预览保留的 MOC 归属已不存在，未继续挂载或归档。请结束任务并保留已完成结果，再按当前资料生成新预览。",
    "A retained MOC membership from the preview is missing. Mounting and archiving did not continue. End the task, keep completed results, and create a new preview from the current files.",
  ],
  maintenance_membership_baseline_changed: [
    "MOC 已在原预览后变化，未继续挂载或归档。请结束任务并保留已完成结果，再按当前资料生成新预览。",
    "A MOC changed after the preview. Mounting and archiving did not continue. End the task, keep completed results, and create a new preview from the current files.",
  ],
  maintenance_close_pause_required: [
    "请先暂停任务，等待当前步骤完成后再核对结束范围。",
    "Pause the task and let the current step finish before reviewing its closing scope.",
  ],
  maintenance_close_recovery_required: [
    "有尚未核对的写入结果，不能直接结束任务。请先核对恢复记录。",
    "Some write outcomes are unverified. Review recovery records before ending the task.",
  ],
  cloud_recovery_index_not_ready: [
    "文件已在本机，但增量观察尚未就绪。请到知识库检查索引状态。",
    "The file is local, but incremental observation is not ready. Check the vault index.",
  ],
  partial_note_scope_changed: [
    "可读范围发生变化，已停止写入。请核对后重新预览。",
    "The readable scope changed. Writing stopped; review and preview again.",
  ],
  partial_note_scope_invalid: [
    "暂不可读范围无效，未继续写入。",
    "The unavailable scope is invalid. Writing did not continue.",
  ],
  partial_note_scope_truncated: [
    "不可读项目过多，当前预览不覆盖全部范围。请先恢复部分资料再重试。",
    "Too many items are unavailable to safely bind this preview. Restore some items and retry.",
  ],
  cloud_recovery_file_changed: [
    "文件位置或版本已变化，请重新检查状态。",
    "The file location or version changed. Check its status again.",
  ],
  cloud_recovery_status_timeout: [
    "单文件状态检查超时；没有请求下载，可稍后手动重试。",
    "The file status check timed out. No download was requested; retry manually later.",
  ],
  cloud_recovery_download_unknown: [
    "下载请求结果尚不确定。请检查此文件状态，不会自动重复请求。",
    "The download request outcome is uncertain. Check this file; the request will not repeat automatically.",
  ],
  cloud_recovery_status_unavailable: [
    "无法读取系统文件状态，请在 Finder 中核对。",
    "System file status is unavailable. Check it in Finder.",
  ],
  cloud_recovery_macos_required: [
    "此操作需要在 macOS 后台运行。",
    "This operation requires a macOS backend.",
  ],
  cloud_status_helper_required: [
    "单文件恢复工具不可用，请使用更新后的桌面启动器。",
    "The file recovery tool is unavailable. Use the updated desktop launcher.",
  ],
  maintenance_link_repair_none_pending: [
    "当前任务没有待核对链接。",
    "This task has no pending links.",
  ],
  maintenance_link_repair_task_not_ready: [
    "请先完成原整理任务，再预览链接修复。",
    "Complete the original organization task before previewing link repairs.",
  ],
  maintenance_link_repair_preview_changed: [
    "修复预览或权限已变化，请重新预览。",
    "The repair preview or permissions changed. Create a new preview.",
  ],
  maintenance_link_repair_source_changed: [
    "笔记在修复预览后发生变化，当前内容已保留。请重新预览；如有中断记录，先核对任务诊断。",
    "The note changed after the repair preview. Current content is preserved. Preview again; review diagnostics if a repair was interrupted.",
  ],
  maintenance_link_repair_destination_changed: [
    "归档目标已变化，已停止链接修复，请核对当前位置。",
    "The archive destination changed. Link repair stopped; check its current location.",
  ],
  maintenance_link_repair_old_path_reused: [
    "旧路径已被其他文件占用，不能自动判断引用，请人工核对。",
    "Another file occupies the old path. References require manual review.",
  ],
  maintenance_link_repair_mapping_ambiguous: [
    "路径映射存在歧义，请人工核对。",
    "The path mapping is ambiguous. Review it manually.",
  ],
  maintenance_link_repair_source_ambiguous: [
    "待核对来源版本不一致，请人工核对。",
    "Pending source versions disagree. Review them manually.",
  ],
  maintenance_link_repair_scope_changed: [
    "引用范围或权限发生变化，请重新核对。",
    "The reference scope or permissions changed. Review again.",
  ],
  maintenance_link_repair_not_ready: [
    "文件仍未就绪或修复预览已过期，请恢复文件后重新预览。",
    "Files are unavailable or the repair preview expired. Make them readable and preview again.",
  ],
  maintenance_undo_link_repair_completed: [
    "此任务已有独立确认的链接修复，不能用原整理撤销覆盖。请先人工核对相关链接。",
    "This task has a separately confirmed link repair. The original undo cannot overwrite it; review the links manually.",
  ],

  organization_catalog_pending: [
    "目录变化尚未核对完成，暂不能生成完整预览。请待索引状态稳定后手动刷新。",
    "Directory changes have not been reconciled. Wait for a stable index, then refresh manually.",
  ],
  organization_catalog_changed: [
    "读取期间目录发生变化，请按当前内容重新预览。",
    "The directory changed during this read. Create a preview from the current content.",
  ],
  organization_moc_unavailable: [
    "目标 MOC 暂不可读，无法核对已有归属。请先让该 MOC 恢复本地可读，再重新预览。",
    "A target MOC is unreadable, so existing memberships cannot be verified. Make it locally readable, then preview again.",
  ],
  organization_archive_scope_incomplete: [
    "已生成可读资料的预览。归档仍需核对全部相关引用，请让暂不可读的资料恢复后重新预览；当前未执行写入或移动。",
    "A preview of readable items is available. Archiving requires checking all related links. Make unavailable items readable and preview again; nothing has been written or moved.",
  ],
  cloud_item_not_downloaded: [
    "所需文件的当前内容尚未在本地可读。请在 Finder 或 Obsidian 中确认可打开后再试。",
    "The current file content is not locally readable. Confirm it opens in Finder or Obsidian, then retry.",
  ],
  cloud_metadata_unavailable: [
    "暂时无法核对 iCloud 文件状态，请稍后手动重试。",
    "iCloud file status could not be verified. Retry manually later.",
  ],
  cloud_item_changed: [
    "文件在读取期间发生变化，请按当前内容重新预览。",
    "The file changed during reading. Preview the current content again.",
  ],
  organization_execution_pause_required: [
    "任务正在执行，请先暂停，待当前安全步骤完成后再进行其他任务操作。",
    "The task is running. Pause it and wait for the current safe step before changing it.",
  ],
  storage_busy: [
    "后台资源暂被占用，本次操作尚未开始，请稍后重试。",
    "Backend resources are temporarily occupied. This operation has not started; try again shortly.",
  ],
  display_busy: [
    "页面读取暂时繁忙，请稍后刷新。",
    "Page reading is temporarily busy. Refresh shortly.",
  ],
  display_changed: [
    "读取期间相关数据发生变化，本次结果已丢弃，请刷新后重试。",
    "Relevant data changed during this read. The result was discarded; refresh to try again.",
  ],
  display_limit_exceeded: [
    "页面数据超过安全读取上限，本次未返回结果。",
    "Page data exceeds the safe read limit. No result was returned.",
  ],
  storage_unavailable: [
    "无法读取本地存储，请检查目录、文件权限或存储状态后重试。",
    "Local storage could not be read. Check the directory, file permissions or storage status before retrying.",
  ],
  backup_snapshot_busy: [
    "正在创建一致性备份，请稍后重试。",
    "Creating a consistent backup. Please retry shortly.",
  ],
  organization_control_busy: [
    "暂停请求尚未提交，授权存储暂忙，请稍后重试。",
    "The pause request was not submitted because authorization storage is busy. Try again shortly.",
  ],
  organization_progress_busy: [
    "整理进度读取暂时繁忙，稍后自动更新。",
    "Organization progress reading is temporarily busy; it will refresh shortly.",
  ],
};
export const retryableReadFailure = (error) =>
  error?.status === 503 &&
  ["storage_busy", "display_busy", "display_changed"].includes(error.code);
export const temporaryReadFailure = (error) =>
  retryableReadFailure(error) ||
  (error?.status === 503 &&
    ["backup_snapshot_busy", "organization_progress_busy"].includes(
      error.code,
    ));
