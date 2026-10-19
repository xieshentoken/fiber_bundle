// Save-rule transport. Directory queries never request note content or run work.
const id = encodeURIComponent;
const record = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const directoryPage = (v) =>
  record(v) &&
  typeof v.snapshot === "string" &&
  typeof v.truncated === "boolean" &&
  (v.next_cursor === null || typeof v.next_cursor === "string") &&
  Array.isArray(v.directories) &&
  v.directories.every(
    (n) =>
      record(n) &&
      typeof n.relative_path === "string" &&
      typeof n.name === "string" &&
      typeof n.selectable === "boolean",
  );
const settings = (v) =>
  record(v) &&
  Number.isInteger(v.revision) &&
  record(v.settings) &&
  ([undefined, 1].includes(v.settings.policy_version) ||
    (v.settings.policy_version === 2 &&
      ["moc_dir", "book_dir", "attachment_root", "original_dir"].every(
        (k) => typeof v.settings[k] === "string" && v.settings[k].length > 0,
      ) &&
      ["off", "preview", "automatic"].includes(v.settings.moc_archive_mode) &&
      v.settings.archive_on_moc === false)) &&
  ["legacy_bundle", "obsidian_folders"].includes(v.settings.layout_mode) &&
  ["inbox_dir", "archive_dir", "image_dir"].every(
    (k) => typeof v.settings[k] === "string",
  ) &&
  Array.isArray(v.mocs) &&
  v.mocs.every(
    (m) => record(m) && typeof m.id === "string" && typeof m.title === "string",
  ) &&
  record(v.settings.moc_destinations) &&
  typeof v.settings.archive_on_moc === "boolean" &&
  ["obsidian", "markdown"].includes(v.settings.image_link_style) &&
  typeof v.publication_ready === "boolean";
const preview = (v) =>
  record(v) &&
  typeof v.preview_id === "string" &&
  Number.isInteger(v.expected_revision) &&
  record(v.paths) &&
  Object.values(v.paths).every((p) => typeof p === "string") &&
  Array.isArray(v.create_directories) &&
  v.create_directories.every((p) => typeof p === "string") &&
  typeof v.publication_ready === "boolean";

const automaticPolicy = (v) =>
  record(v) &&
  Number.isInteger(v.revision) &&
  record(v.settings) &&
  ["manual", "automatic"].includes(v.settings.review_mode) &&
  ["rules", "model"].includes(v.settings.review_method) &&
  ["manual", "automatic"].includes(v.settings.publish_mode) &&
  Array.isArray(v.settings.allowed_kinds) &&
  record(v.capabilities);
const automaticJobs = (v) =>
  Array.isArray(v) &&
  v.length <= 100 &&
  v.every(
    (job) =>
      record(job) &&
      ["id", "kind", "phase", "state"].every(
        (key) => typeof job[key] === "string",
      ),
  );
const attachmentProjections = (v) =>
  Array.isArray(v) &&
  v.length <= 100 &&
  v.every(
    (item) =>
      record(item) &&
      ["id", "state", "original_name", "path"].every(
        (key) => typeof item[key] === "string",
      ),
  );

export function createVaultSaveApi(request) {
  const path = (vault) => `/admin/vault-catalog/${id(vault)}`;
  return {
    discover: (body) =>
      request(
        "/admin/vault-catalog/directory-preview",
        "POST",
        body,
        directoryPage,
      ),
    directories: (vault, parent = "", cursor = null) =>
      request(
        path(vault) +
          "/directories?relative_parent=" +
          id(parent) +
          (cursor ? "&cursor=" + id(cursor) : ""),
        "GET",
        undefined,
        directoryPage,
      ),
    get: (vault) =>
      request(path(vault) + "/save-policy", "GET", undefined, settings),
    preview: (vault, body) =>
      request(path(vault) + "/save-policy/preview", "POST", body, preview),
    save: (vault, body) =>
      request(path(vault) + "/save-policy", "PUT", body, settings),
    automaticPolicy: (vault) =>
      request(
        path(vault) + "/automatic-policy",
        "GET",
        undefined,
        automaticPolicy,
      ),
    saveAutomaticPolicy: (vault, body) =>
      request(path(vault) + "/automatic-policy", "PUT", body, automaticPolicy),
    attachmentProjections: (vault) =>
      request(
        path(vault) + "/attachment-projections",
        "GET",
        undefined,
        attachmentProjections,
      ),
    reconcileAttachment: (vault, operation) =>
      request(
        path(vault) + `/attachment-projections/${id(operation)}/reconcile`,
        "POST",
        {},
      ),
    automaticJobs: (vault) =>
      request(path(vault) + "/automatic-jobs", "GET", undefined, automaticJobs),
    reconcileAutomaticJob: (vault, job, approveNewModelAttempt = false) =>
      request(
        `/admin/vault-catalog/${id(vault)}/automatic-jobs/${id(job)}/reconcile`,
        "POST",
        { approve_new_model_attempt: approveNewModelAttempt },
      ),
    cancelAutomaticJob: (vault, job) =>
      request(
        path(vault) + "/automatic-jobs/" + id(job) + "/cancel",
        "POST",
        {},
      ),
    publishAutomaticJob: (vault, job) =>
      request(
        path(vault) + "/automatic-jobs/" + id(job) + "/publish",
        "POST",
        {},
      ),
    abandon: (vault, operation_id) =>
      request(path(vault) + "/save-policy/abandon", "POST", {
        operation_id,
        preserve_directories: true,
      }),
  };
}

const errors = {
  save_policy_attachment_parent_required:
    "图片与非图片目录须是附件根目录下互不重叠的子目录。",
  save_policy_revision_conflict:
    "保存规则已被修改。请重新打开规则，核对后再保存。",
  save_policy_preview_changed: "目录或权限已变化，请刷新目录并重新预览。",
  save_policy_reconciliation_required:
    "上次目录创建未完成，请先核对并结束待处理操作。",
  save_policy_read_only: "该仓库为只读，不能保存写入规则或创建目录。",
  save_policy_directory_overlap:
    "笔记、MOC、专家包与附件的目录用途冲突，请选择互不覆盖的目录。",
  save_policy_scope_denied: "所选目录不在允许写入的范围内，请调整选择。",
  save_policy_path_alias:
    "目录名称存在大小写或字符形式冲突，请从已发现目录中选择。",
  save_policy_directory_not_writable: "目录不可写，请检查本机文件权限。",
  save_policy_moc_unavailable: "MOC 已变化或不可用，请重新打开保存规则。",
  save_policy_directory_limit: "目录内容过多，请缩小配置范围。",
  vault_directories_preview_expired: "目录预览已过期，请重新读取目录并预览。",
  vault_directories_directory_changed:
    "仓库或目录已变化，请重新选择并刷新目录。",
  vault_directories_stale_cursor: "目录列表已变化，请刷新后重新展开。",
  vault_directories_scope_denied: "目录不在允许访问的范围内。",
  vault_directories_preview_limit: "未完成的预览较多，请稍后重新读取目录。",
};
export const vaultSaveErrorText = (code) => errors[code];
