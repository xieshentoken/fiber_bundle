// Shared bounded HTTP errors; no server exception text or operation replay policy in views.
export const requestErrors = {
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
