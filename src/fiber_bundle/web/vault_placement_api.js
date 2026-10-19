// Authenticated placement and optional single-publication conversion transport.
const path = encodeURIComponent;
const packet = (v) =>
  v &&
  typeof v.operation_id === "string" &&
  typeof v.ready === "boolean" &&
  Array.isArray(v.unresolved_links);
export function createVaultPlacementApi(request) {
  const scope = (client, vault) =>
    `?client_id=${path(client)}&vault_id=${path(vault)}`;
  const placement = "/admin/vault-placements",
    migration = "/admin/vault-layout-migrations",
    rebind = "/admin/vault-publication-rebinds";
  return {
    mocList: (client, vault) =>
      request(
        placement + "/mocs" + scope(client, vault),
        "GET",
        undefined,
        (v) => v && Array.isArray(v.candidates) && Array.isArray(v.operations),
      ),
    mocPreview: (body) =>
      request(
        placement + "/mocs/preview",
        "POST",
        body,
        (v) =>
          v &&
          typeof v.operation_id === "string" &&
          typeof v.markdown === "string" &&
          typeof v.path === "string" &&
          Number.isInteger(v.manual_bytes_preserved),
      ),
    mocAction: (id, action, body) =>
      request(`${placement}/mocs/${path(id)}/${action}`, "POST", {
        ...body,
        confirm: true,
      }),
    list: (client, vault) => request(placement + scope(client, vault)),
    automaticAction: (id, action, body) =>
      request(`${placement}/automatic/${path(id)}/${action}`, "POST", {
        ...body,
        confirm: true,
      }),
    missingPreview: (body) =>
      request(placement + "/missing/preview", "POST", body),
    missingApply: (id, body) =>
      request(`${placement}/missing/${path(id)}/apply`, "POST", {
        ...body,
        confirm: true,
      }),
    rebindPreview: (body) => request(rebind + "/preview", "POST", body, packet),
    scanExternal: (body) => request(rebind + "/scan", "POST", body),
    externalPreview: (body) => request(rebind + "/batch/preview", "POST", body),
    externalRead: (id, client, vault) =>
      request(`${rebind}/batch/${path(id)}${scope(client, vault)}`),
    externalApply: (id, body) =>
      request(`${rebind}/batch/${path(id)}/apply`, "POST", {
        ...body,
        confirm: true,
        max_items: 24,
      }),
    rebindAction: (id, action, body) =>
      request(`${rebind}/${path(id)}/${action}`, "POST", {
        ...body,
        confirm: true,
      }),
    preview: (body) => request(placement + "/preview", "POST", body, packet),
    action: (id, action, body) =>
      request(`${placement}/${path(id)}/${action}`, "POST", {
        ...body,
        confirm: true,
      }),
    legacy: (client, vault) =>
      request(
        migration + scope(client, vault),
        "GET",
        undefined,
        Array.isArray,
      ),
    migrationPreview: (body) =>
      request(migration + "/preview", "POST", body, packet),
    migrationAction: (id, action, body) =>
      request(`${migration}/${path(id)}/${action}`, "POST", {
        ...body,
        confirm: true,
      }),
  };
}
const errors = {
  layout_tracked_note_replaced:
    "普通笔记或 Map 的文件身份已变化，请在批量核对外部改动中保留原身份重新绑定。",
  layout_note_identity_vault_changed:
    "登记的知识库目录身份已变化，请先核对知识库位置。",
  layout_reconcile_duplicate_selection: "同一份资料重复选择，请重新扫描。",
  layout_reconcile_deletion_review_required:
    "尚有正文或候选位置，或未确认删除；请核对位置，不会自动标记缺失。",
  layout_reconcile_storage_unavailable:
    "本项未完成，请核对后台与文件状态后恢复；历史和原文件保留。",
  layout_rebind_revision_changed: "位置记录已变化，请重新扫描并预览。",
  layout_rebind_identity_collision: "目标已属于另一份资料，不能重复绑定。",
  layout_rebind_scope_denied: "目标不在当前允许范围，不能重新绑定。",
  layout_rebind_whole_package_required:
    "资料包需要保持完整目录，请填写包内 note.md 路径。",
  layout_rebind_package_members_changed:
    "资料包成员发生变化，请逐项核对，不能拆包绑定。",
  layout_rebind_invalid_entry: "请填写允许范围内的 Markdown 相对路径。",
  placement_confirmation_required: "请先核对预览并明确确认。",
  placement_expert_explicit_archive_required:
    "完整专家 Draft 请使用专门的整包归档入口，目标取自保存规则。",
  placement_expert_package_required: "当前选择的内容不是完整专家 Draft。",
  placement_expert_internal_link_change:
    "归档会影响包内链接，为保留原包完整性已停止，请先核对引用。",
  moc_registration_file_changed:
    "MOC 文件、目录或规则已变化，请重新读取并预览。",
  moc_registration_already_registered:
    "该 MOC 已接入，请刷新后在关联列表选择。",
  moc_registration_preview_expired: "MOC 接入预览已过期，请重新预览。",
  moc_registration_requires_recovery: "MOC 接入尚未完成，请核对恢复记录。",
  moc_registration_foreign_block: "该文件已有其他系统关联区，请先核对归属。",
  placement_moc_projection_changed:
    "MOC 目录文件或改写记录已变化，请刷新并重新预览。",
  layout_missing_body_still_present:
    "正文或部分包仍存在，请使用位置重新绑定，不可标记缺失。",
  layout_missing_published_source_required:
    "发布状态已变化，请刷新后重新核对。",

  layout_rebind_required:
    "发布内容已在外部移动或替换，请先在整理面板重新绑定位置。",
  layout_rebind_original_still_present:
    "原文件仍存在，不能将另一个副本合并为同一身份。",
  layout_rebind_preview_changed: "文件或配置已变化，请重新预览位置绑定。",
  layout_rebind_resource_changed:
    "包内资源摘要变化，请先人工核对；文件已保留。",
  placement_active_policy_required: "请先在保存规则中预览并启用标题笔记布局。",
  placement_note_changed: "笔记或其他链接内容已变化，请重新读取并预览。",
  placement_policy_or_vault_changed:
    "规则、目录或权限已变化；请保留当前文件并重新核对。",
  placement_reconciliation_required: "存在未完成的操作，请先核对恢复记录。",
  placement_moc_changed: "MOC 归属或版本已变化，请重新读取并预览。",
  placement_preview_not_ready: "请先解决目标目录选择或待修复链接。",
  placement_destination_exists:
    "目标已存在同名内容，请调整归档映射后重新预览。",
  placement_already_at_destination: "笔记已位于所选归档目录。",
  placement_preview_expired: "预览已过期，请重新预览。",
  layout_migration_source_changed: "旧包已变化，请重新预览；原文件已保留。",
  layout_migration_untracked_files_require_review:
    "旧包包含未登记文件，请先人工核对。",
  layout_migration_resource_changed: "旧包附件已变化，请先人工核对。",
  layout_policy_reconfirmation_required:
    "保存规则绑定失效，请重新选择目录并确认启用。",
};
export const vaultPlacementErrorText = (code) => errors[code];
