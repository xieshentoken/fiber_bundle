// Identity-preserving location changes use their own admin intents, never detach/attach.
const record = (v) => v && typeof v === "object" && !Array.isArray(v);
const states = new Set([
  "prepared",
  "quiescing",
  "checkpointing",
  "moving",
  "switching",
  "verifying",
  "restoring",
  "needs_recovery",
  "complete",
  "restored",
  "cancelled",
]);
export const relocationActive = (v) =>
  v && !["prepared", "complete", "restored", "cancelled"].includes(v.state);
export const relocationStatus = (v) =>
  record(v) &&
  typeof v.id === "string" &&
  typeof v.vault_id === "string" &&
  states.has(v.state) &&
  typeof v.source === "string" &&
  typeof v.destination === "string" &&
  typeof v.plan_sha256 === "string" &&
  record(v.preservation) &&
  Array.isArray(v.completed_phases);
const base = "/admin/vault-relocations";
const id = encodeURIComponent;
export function createVaultRelocationApi(request) {
  return {
    list: (vault) =>
      request(
        `${base}?vault_id=${id(vault)}`,
        "GET",
        undefined,
        (v) => Array.isArray(v) && v.every(relocationStatus),
      ),
    preview: (vault_id, destination) =>
      request(
        `${base}/preview`,
        "POST",
        { vault_id, destination },
        relocationStatus,
      ),
    status: (op) =>
      request(`${base}/${id(op)}`, "GET", undefined, relocationStatus),
    start: (op, plan_sha256) =>
      request(
        `${base}/${id(op)}/start`,
        "POST",
        { plan_sha256, external_writers_stopped: true },
        relocationStatus,
      ),
    stop: (op, plan_sha256) =>
      request(
        `${base}/${id(op)}/stop`,
        "POST",
        { plan_sha256 },
        relocationStatus,
      ),
    recoveryPreview: (op) =>
      request(
        `${base}/${id(op)}/recovery-preview`,
        "POST",
        undefined,
        relocationStatus,
      ),
    recover: (op, action, recovery_sha256) => {
      if (!["resume", "restore"].includes(action))
        throw new Error("不支持的迁移恢复操作");
      return request(
        `${base}/${id(op)}/recover`,
        "POST",
        { action, recovery_sha256 },
        relocationStatus,
      );
    },
  };
}
const errors = {
  relocation_legacy_unsupported: "此库由启动配置管理，首版暂不支持迁移。",
  relocation_in_progress: "该知识库正在迁移，查询和写入暂时等待。",
  relocation_location_changed: "知识库位置已变化，请刷新后重试。",
  relocation_target_exists:
    "目录已被占用。请选择尚不存在的目录；不会覆盖已有文件。",
  relocation_same_disk_required: "首版仅支持同一磁盘，跨磁盘迁移暂未开放。",
  relocation_local_folder_required:
    "请选择本机普通目录；云同步目录和网络盘暂不支持。",
  relocation_platform_unsupported: "首版本机迁移目前只支持 macOS。",
  relocation_full_scope_required:
    "此库存在排除或受保护范围，无法核验整库，暂不能迁移。",
  relocation_overlap: "目标与原库、其他知识库或应用状态目录重叠，请重新选择。",
  relocation_invalid_target: "目标须为完整绝对路径，且父目录已存在。",
  relocation_permission_required:
    "没有移动目录所需的系统权限，请检查原位置与目标父目录。",
  relocation_space_required: "磁盘空间不足以建立迁移恢复点。",
  relocation_busy: "本库仍有任务执行或消息发送，请等待结束后重新预检。",
  relocation_operation_conflict: "本库存在未完成的整理、合并或恢复，请先处理。",
  relocation_preview_changed: "预检后内容或条件已变化，请重新预检。",
  relocation_preview_expired: "确认已过期，请重新预检。",
  relocation_source_changed:
    "文件内容或身份发生变化，已停止迁移。请保留现场并核对。",
  relocation_bindings_changed: "知识库历史、权限或关联发生变化，须先核对。",
  relocation_directory_changed: "目录身份与记录不符，不能自动继续或覆盖恢复。",
  relocation_parent_changed: "原位置或目标父目录已变化，请先核对。",
  relocation_policy_changed: "目录的访问策略已变化，已停止迁移。",
  relocation_links_or_mounts:
    "库中包含链接、特殊文件或嵌套挂载点，首版无法整库核验。",
  relocation_absolute_links:
    "文件中存在指向旧根目录的绝对链接，请先处理后重新预检。",
  relocation_inventory_limit:
    "仓库超过首版核验上限（2 GiB、20,000 项或单次 60 秒）。",
  relocation_checkpoint_invalid: "恢复点无法通过完整性检查，文件移动已停止。",
  relocation_interrupted: "检测到未完成迁移，请先核对现场再继续或恢复原位置。",
  relocation_stopped: "已安全停止后续步骤。核对现场后可继续或恢复原位置。",
  relocation_recovery_unavailable: "当前条件不允许此恢复操作，请重新核对现场。",
  relocation_state_changed: "迁移状态已变化，请刷新后再操作。",
  relocation_storage_unavailable:
    "迁移遇到存储问题，现场已保留。请核对目录和磁盘。",
};
export function relocationErrorText(code) {
  return errors[code] || null;
}
