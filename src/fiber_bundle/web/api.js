import { requestErrors, retryableReadFailure } from "./request_errors.js";
import { createSupplierApi } from "./suppliers_api.js";
import { waitForReadRetry } from "./read_retry.js";
import { createChannelServicesApi } from "./channel_services_api.js";
import { createBackupAutomationApi } from "./backup_automation_api.js";
import {
  createVaultRelocationApi,
  relocationErrorText,
} from "./vault_relocation_api.js";
import { createOrganizationApi } from "./organization_api.js";
import { createVideoApi } from "./video_api.js";
import { createModelsApi } from "./models_api.js";
import { createSkillExportApi } from "./skill_export_api.js";
import { createHomeApi } from "./home_api.js";
import { createVaultMergeApi } from "./vault_merge_api.js";
import { createAgentApi, agentErrorText } from "./agent_api.js";
import { channelQr } from "./channel_qr.js";
import { noteIndexErrorText } from "./note_index_errors.js";
import { createGraphApi } from "./knowledge_graph_api.js";
import {
  createVaultPlacementApi,
  vaultPlacementErrorText,
} from "./vault_placement_api.js";
import { createVaultSaveApi, vaultSaveErrorText } from "./vault_save_api.js";
import { createSystemApi } from "./system_api.js";
import { vaultCatalogErrorText } from "./vault_catalog_errors.js";
import { createChannelApi, channelErrorText } from "./channel_api.js";
// The only HTTP dependency of the frontend. Feature views use named operations.
export class ApiError extends Error {
  constructor(message, status = 0, code = "", issues = [], field = "") {
    super(message);
    this.status = status;
    this.code = code;
    this.issues = issues;
    this.field = field;
  }
}
const errors = {
  403: "请求未获许可，请刷新会话后重试。",
  404: "资源不可用或权限已撤销，请刷新。",
  409: "当前记录已变化，请刷新并核对后重试。",
  422: "输入不符合要求，请检查名称与权限。",
  429: "登录尝试过多，请稍后重试。",
  503: "后台存储暂不可用，请检查本机后台。",
};
function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function flag(value) {
  return value === 0 || value === 1 || typeof value === "boolean";
}
function named(value) {
  return (
    record(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string"
  );
}
function vaultList(value) {
  return (
    Array.isArray(value) &&
    value.every(
      (v) =>
        named(v) &&
        [
          "active",
          "pending",
          "detached",
          "trash_pending",
          "trashed",
          "reconcile",
        ].includes(v.status) &&
        flag(v.default_query) &&
        (v.requested_by_client_id === null ||
          typeof v.requested_by_client_id === "string"),
    )
  );
}
function clientList(value) {
  return (
    Array.isArray(value) &&
    value.every(
      (c) =>
        named(c) &&
        flag(c.active) &&
        flag(c.create_vault) &&
        Array.isArray(c.grants) &&
        c.grants.every(
          (g) =>
            record(g) &&
            typeof g.vault_id === "string" &&
            flag(g.can_query) &&
            flag(g.can_original) &&
            flag(g.can_add),
        ),
    )
  );
}
function batchList(value) {
  return (
    Array.isArray(value) &&
    value.every(
      (batch) =>
        record(batch) &&
        typeof batch.id === "string" &&
        ["buffering", "frozen", "selected"].includes(batch.state) &&
        Number.isInteger(batch.count) &&
        batch.count > 0 &&
        Number.isFinite(batch.received_at) &&
        (batch.freeze_at === undefined ||
          batch.freeze_at === null ||
          (Number.isFinite(batch.freeze_at) && batch.freeze_at > 0)) &&
        typeof batch.preview === "string" &&
        (batch.intake === undefined ||
          batch.intake === null ||
          (record(batch.intake) &&
            typeof batch.intake.state === "string" &&
            Array.isArray(batch.intake.links) &&
            batch.intake.links.every(
              (link) => record(link) && typeof link.state === "string",
            ) &&
            Array.isArray(batch.intake.jobs) &&
            batch.intake.jobs.every(
              (job) =>
                record(job) &&
                typeof job.state === "string" &&
                typeof job.phase === "string",
            ))) &&
        typeof batch.content_ready === "boolean" &&
        typeof batch.needs_question === "boolean",
    )
  );
}
function backupItem(value) {
  return (
    record(value) &&
    typeof value.id === "string" &&
    ["create", "restore"].includes(value.action) &&
    typeof value.state === "string" &&
    typeof value.destination === "string" &&
    typeof value.command === "string" &&
    value.terminal_password_required === true &&
    value.services_started === false
  );
}
function backupList(value) {
  return Array.isArray(value) && value.length <= 50 && value.every(backupItem);
}
const pathId = (value) => encodeURIComponent(value);

export function createAdminApi(
  session,
  { onConnection, onUnauthorized, onMutation = () => {} },
  fetcher = fetch,
) {
  async function request(
    path,
    method = "GET",
    body,
    validate = record,
    options = {},
  ) {
    if (method !== "GET") onMutation();
    if (options.signal?.aborted)
      throw new ApiError("读取已取消。", 0, "cancelled");
    const revision = session.revision,
      controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    const membershipWrite =
      method === "POST" &&
      [
        "/admin/organization/members",
        "/admin/organization/members/batch",
      ].includes(path);
    let timedOut = false;
    const timeout = setTimeout(
      () => {
        timedOut = true;
        controller.abort();
      },
      path === "/admin/knowledge-graph/runtime/detect" ||
        path === "/admin/note-index/update" ||
        membershipWrite
        ? 120000
        : path === "/admin/project-skills" || path === "/admin/imports"
          ? 60000
          : path.startsWith("/admin/models/runtime")
            ? 35000
            : path === "/admin/models/discover"
              ? 30000
              : path.startsWith("/admin/organization") ||
                  path.startsWith("/admin/note-index/status") ||
                  path.startsWith("/admin/vault-") ||
                  path.startsWith("/admin/knowledge-graph/")
                ? 60000
                : 10000,
    );
    let response;
    try {
      try {
        response = await fetcher(path, {
          method,
          credentials: "same-origin",
          signal: controller.signal,
          headers: {
            "Content-Type": "application/json",
            ...(session.csrf ? { "X-CSRF-Token": session.csrf } : {}),
          },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
      } catch {
        if (options.signal?.aborted)
          throw new ApiError("读取已取消。", 0, "cancelled");
        if (!timedOut || !membershipWrite) onConnection(false);
        throw new ApiError(
          timedOut
            ? "本次读取超时，请稍后刷新。"
            : "无法连接本机后台，请确认后台已启动后刷新。",
          0,
          timedOut ? "timeout" : "network",
        );
      }
      if (options.signal?.aborted)
        throw new ApiError("读取已取消。", 0, "cancelled");
      onConnection(true);
      // A response from an older session must not repopulate cleared data or end a new session.
      if (revision !== session.revision)
        throw new ApiError("会话已变化，请刷新后重试。", 409);
      if (!response.ok) {
        if (response.status === 401) {
          onUnauthorized();
          throw new ApiError(
            path === "/admin/login"
              ? "密码不正确，或尚未设置管理密码。"
              : "会话已失效，请重新登录。",
            401,
          );
        }
        let detail,
          code = "",
          issues = [],
          field = "";
        {
          try {
            const body = await response.json();
            if (
              record(body) &&
              typeof body.error === "string" &&
              /^[a-z][a-z0-9_]{0,95}$/.test(body.error)
            )
              code = body.error;
            if (record(body) && path.startsWith("/admin/agent-connections")) {
              detail = agentErrorText(body.error);
              if (detail) detail += `（${body.error}）`;
            }
            if (record(body) && path.startsWith("/admin/channels")) {
              issues = Array.isArray(body.issues)
                ? body.issues
                    .filter(
                      (code) =>
                        typeof code === "string" && /^[a-z_]{1,80}$/.test(code),
                    )
                    .slice(0, 20)
                : [];
              detail = channelErrorText(body.error, issues);
            }
            if (record(body) && path.startsWith("/admin/vault-"))
              detail =
                relocationErrorText(body.error) ||
                vaultCatalogErrorText(body.error) ||
                vaultSaveErrorText(body.error) ||
                vaultPlacementErrorText(body.error);
            if (record(body) && path.startsWith("/admin/note-index"))
              detail = noteIndexErrorText(body.error);
            if (Object.hasOwn(requestErrors, code))
              detail = requestErrors[code][0];
            if (record(body) && body.error === "event_conflict")
              detail = "事件内容冲突。";
            if (
              record(body) &&
              typeof body.message === "string" &&
              body.message.length <= 500
            )
              detail = body.message;
            if (
              record(body) &&
              typeof body.field === "string" &&
              /^[a-z_]{1,80}$/.test(body.field)
            )
              field = body.field;
            if (record(body) && body.error === "backup_unclassified_state")
              detail =
                "状态目录含未分类资料，备份准备已阻止。请先核对备份范围；不要删除原件。";
            if (
              record(body) &&
              [
                "document_split_unit_limit",
                "document_split_total_limit",
                "document_split_part_limit",
              ].includes(body.error)
            )
              detail =
                "候选已保留：完整段落、代码块或总输出超过冻结发布限额。请调整新任务额度后重新导入；不会截断发布。";
            if (record(body) && body.error === "document_split_reference_scope")
              detail =
                "候选已保留：跨段引用、文内锚点或 HTML 无法安全分片。请使用足够容纳单文件的新任务额度。";
            if (record(body) && body.error === "note_index_update_required")
              detail =
                "笔记已变化，全文索引需要更新。更新完成前不会交付旧内容。";
            if (record(body) && body.error === "note_index_match_limit")
              detail = "匹配内容过多，请缩小知识库范围或补充更具体的关键词。";
          } catch {
            // Only a recognized bounded code may override the local fallback message.
          }
          if (revision !== session.revision)
            throw new ApiError("会话已变化，请刷新后重试。", 409);
        }
        if (
          retryableReadFailure({ status: response.status, code }) &&
          method === "GET" &&
          options.retryBusy !== false
        ) {
          if (!(await waitForReadRetry(options.signal)))
            throw new ApiError("读取已取消。", 0, "cancelled");
          return request(path, method, body, validate, {
            ...options,
            retryBusy: false,
          });
        }
        throw new ApiError(
          detail || errors[response.status] || "操作未完成，请刷新后重试。",
          response.status,
          code,
          issues,
          field,
        );
      }
      let data;
      if (validate === null) {
        const blob = await response.blob();
        if (options.signal?.aborted)
          throw new ApiError("读取已取消。", 0, "cancelled");
        if (revision !== session.revision)
          throw new ApiError("会话已变化，请刷新后重试。", 409);
        return {
          blob,
          disposition: response.headers?.get("Content-Disposition"),
        };
      }
      try {
        data = await response.json();
      } catch {
        if (options.signal?.aborted)
          throw new ApiError("读取已取消。", 0, "cancelled");
        if (timedOut)
          throw new ApiError("本次读取超时，请稍后刷新。", 0, "timeout");
        throw new ApiError("后台响应格式无效。");
      }
      if (options.signal?.aborted)
        throw new ApiError("读取已取消。", 0, "cancelled");
      if (revision !== session.revision)
        throw new ApiError("会话已变化，请刷新后重试。", 409);
      if (!validate(data)) throw new ApiError("后台响应格式无效。");
      return data;
    } finally {
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abort);
    }
  }
  async function download(path, name) {
    const { blob, disposition } = await request(path, "GET", undefined, null);
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    let serverName;
    try {
      const match = disposition?.match(/filename\*=UTF-8''([^;]+)/i);
      serverName = match
        ? decodeURIComponent(match[1])
        : disposition?.match(/filename="([^"]+)"/)?.[1];
    } catch {
      /* Invalid headers retain the explicit local fallback. */
    }
    link.download =
      serverName &&
      !/[\\/]/.test(serverName) &&
      ![...serverName].some(
        (c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127,
      )
        ? serverName
        : name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return {
    suppliers: createSupplierApi(request, pathId),
    modelsApi: createModelsApi(request),
    video: createVideoApi(request),
    ...createSystemApi(request),
    ...createBackupAutomationApi(request, pathId),
    homeRead: createHomeApi(request),
    vaultPreview: (data) =>
      request("/admin/vault-catalog/preview", "POST", data),
    vaultCreationPreview: (data) =>
      request("/admin/vault-catalog/create-preview", "POST", data),
    vaultPrepare: (data) => request("/admin/vault-catalog/jobs", "POST", data),
    vaultJobs: () =>
      request("/admin/vault-catalog/jobs", "GET", undefined, Array.isArray),
    vaultStart: (id) =>
      request(`/admin/vault-catalog/jobs/${pathId(id)}/start`, "POST"),
    vaultCancel: (id) =>
      request(`/admin/vault-catalog/jobs/${pathId(id)}/cancel`, "POST"),
    ...createChannelApi(request),
    ...createChannelServicesApi(request),
    channels: () => request("/admin/channels", "GET", undefined, Array.isArray),
    channelSave: (body) => request("/admin/channels", "POST", body),
    channelAction: (id, action) =>
      request(`/admin/channels/${pathId(id)}/${pathId(action)}`, "POST"),
    channelConfirmOwner: (id, revision, owner) =>
      request(`/admin/channels/${pathId(id)}/confirm-owner`, "POST", {
        expected_revision: revision,
        owner_id: owner,
      }),
    channelTest: (id, question, model = false) =>
      request(`/admin/channels/${pathId(id)}/test`, "POST", {
        question,
        execute_model: model,
      }),
    channelEvents: (id) =>
      request(
        `/admin/channels/${pathId(id)}/events`,
        "GET",
        undefined,
        Array.isArray,
      ),
    channelReconcile: (id, event_id, acknowledgement) =>
      request(`/admin/channels/${pathId(id)}/reconcile`, "POST", {
        event_id,
        acknowledgement,
      }),
    channelQr: (id) =>
      channelQr(fetcher, session, onUnauthorized, id, ApiError),
    vaultTrashJobs: () =>
      request("/admin/vault-catalog/trash", "GET", undefined, Array.isArray),
    vaultRecovery: (id, root) =>
      request(`/admin/vault-catalog/trash/${pathId(id)}/reconcile`, "POST", {
        confirmed_root: root,
      }),
    vaultTrashPreview: (id) =>
      request(`/admin/vault-catalog/${pathId(id)}/trash-preview`, "POST"),
    vaultTrashConfirm: async (id, root) => {
      if (!globalThis.window?.fiberDesktop?.trashIntent)
        return request(
          `/admin/vault-catalog/trash/${pathId(id)}/confirm`,
          "POST",
          { confirmed_root: root },
        );
      const revision = session.revision;
      let value;
      try {
        value = await window.fiberDesktop.trashIntent({
          id,
          confirmedRoot: root,
          csrf: session.csrf,
        });
      } catch {
        throw new ApiError(
          "未能确认本机废纸篓请求结果。请先检查原目录与系统废纸篓，再刷新核对；不要连续重复删除。",
          0,
          "native_trash_result_unknown",
        );
      }
      if (revision !== session.revision)
        throw new ApiError("会话已变化，请刷新后核对操作状态。", 409);
      if (value?.native_error) {
        const { status, code } = value.native_error;
        if (status === 401) onUnauthorized();
        throw new ApiError(
          status === 401
            ? "会话已失效，请重新登录。"
            : vaultCatalogErrorText(code) ||
                errors[status] ||
                "废纸篓操作未完成，请刷新后核对目录状态。",
          status,
          code,
        );
      }
      return value;
    },
    vaultUnlink: (id) =>
      request(`/admin/vault-catalog/${pathId(id)}/unlink`, "POST"),
    deliveryReconciliation: () =>
      request(
        "/admin/delivery-reconciliation",
        "GET",
        undefined,
        Array.isArray,
      ),
    deliveryFeedback: (data) =>
      request("/admin/delivery-reconciliation", "POST", data, record),
    sources: (client, vault) =>
      request(
        "/admin/sources?client_id=" +
          pathId(client) +
          "&vault_id=" +
          pathId(vault),
        "GET",
        undefined,
        Array.isArray,
      ),
    imports: (client, vault) =>
      request(
        "/admin/imports?client_id=" +
          pathId(client) +
          "&vault_id=" +
          pathId(vault),
        "GET",
        undefined,
        Array.isArray,
      ),
    expertCommands: (client, vault) =>
      request(
        "/admin/imports/expert-commands?client_id=" +
          pathId(client) +
          "&vault_id=" +
          pathId(vault),
        "GET",
        undefined,
        Array.isArray,
      ),
    createImport: (body) => request("/admin/imports", "POST", body),
    importChunk: (id, body) =>
      request("/admin/imports/" + pathId(id) + "/chunks", "PUT", body),
    finishImport: (id, client) =>
      request("/admin/imports/" + pathId(id) + "/finish", "POST", {
        client_id: client,
      }),
    startImport: (id, client) =>
      request("/admin/imports/" + pathId(id) + "/start", "POST", {
        client_id: client,
      }),
    cancelImport: (id, client) =>
      request("/admin/imports/" + pathId(id) + "/cancel", "POST", {
        client_id: client,
      }),
    resumeExpert: (id, body) =>
      request("/admin/imports/" + pathId(id) + "/resume-expert", "POST", body),
    expertArtifact: (id, client) =>
      request(
        "/admin/imports/" + pathId(id) + "/expert?client_id=" + pathId(client),
      ),
    expertQuery: (id, client, question) =>
      request(
        "/admin/imports/" +
          pathId(id) +
          "/expert-query?client_id=" +
          pathId(client) +
          "&question=" +
          encodeURIComponent(question),
      ),
    publishExpert: (id, body) =>
      request("/admin/imports/" + pathId(id) + "/publish-expert", "POST", body),
    noteIndexStatus: (client, vault) =>
      request(
        "/admin/note-index/status?client_id=" +
          pathId(client) +
          "&vault_id=" +
          pathId(vault),
      ),
    updateNoteIndex: (body) =>
      request("/admin/note-index/update", "POST", body),
    sourcePolicy: () => request("/admin/source-policy"),
    projectSkills: () => request("/admin/project-skills"),
    saveProjectSkills: (body) => request("/admin/project-skills", "PUT", body),
    saveSourcePolicy: (body) => request("/admin/source-policy", "PUT", body),
    submitSource: (body) => request("/admin/source-tasks", "POST", body),
    startSource: (id) =>
      request("/admin/source-tasks/" + pathId(id) + "/start", "POST"),
    articles: () => request("/admin/articles"),
    articlePacket: (id) =>
      request("/admin/articles/" + pathId(id) + "/preview"),
    reviewArticle: (id, body) =>
      request("/admin/articles/" + pathId(id) + "/reviews", "POST", body),
    publishArticle: (id, body) =>
      request("/admin/articles/" + pathId(id) + "/publish", "POST", body),
    backend: () => request("/admin/backend"),
    stopBackend: (instance_id) =>
      request("/admin/backend/stop", "POST", { instance_id }),
    reminders: () =>
      request("/admin/backend/reminders", "GET", undefined, Array.isArray),
    setReminder: (id, enabled) =>
      request("/admin/backend/reminders/" + pathId(id), "PUT", { enabled }),
    components: () => request("/admin/inventory/components"),
    checkComponents: () => request("/admin/inventory/components/check", "POST"),
    inventoryReceipts: () =>
      request("/admin/inventory/receipts", "GET", undefined, Array.isArray),
    inventoryExecutions: () => request("/admin/inventory/executions"),
    cancelExecution: (id) =>
      request("/admin/inventory/executions/" + pathId(id) + "/cancel", "POST"),
    reconcileExecution: (id) =>
      request(
        "/admin/inventory/executions/" + pathId(id) + "/reconcile",
        "POST",
      ),
    inventoryVersions: (client, vault, offset) =>
      request(
        "/admin/inventory/versions/" +
          pathId(vault) +
          "?client_id=" +
          pathId(client) +
          "&offset=" +
          offset,
        "GET",
        undefined,
        Array.isArray,
      ),
    downloadOriginal: (client, vault, version, asset) =>
      download(
        "/admin/inventory/original/" +
          [vault, version, asset].map(pathId).join("/") +
          "?client_id=" +
          pathId(client),
        "original.bin",
      ),
    graph: createGraphApi(request),
    organization: createOrganizationApi(request),
    vaultSave: createVaultSaveApi(request),
    vaultMerges: createVaultMergeApi(request),
    vaultRelocations: createVaultRelocationApi(request),
    vaultPlacements: createVaultPlacementApi(request),
    previewPublication: (kind, id, body = {}) =>
      request(
        `/admin/vault-publications/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/preview`,
        "POST",
        body,
      ),
    discardPublicationPreview: (kind, id) =>
      request(
        `/admin/vault-publications/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/discard-preview`,
        "POST",
      ),
    agents: createAgentApi(request),
    skillExports: createSkillExportApi(request),
    creationTasks: () => request("/admin/document-tasks"),
    submitCreation: (body) => request("/admin/document-tasks", "POST", body),
    creationStatus: (id) => request("/admin/document-tasks/" + pathId(id)),
    appendCreation: (id, body) =>
      request("/admin/document-tasks/" + pathId(id) + "/append", "POST", body),
    cancelCreation: (id, body) =>
      request("/admin/document-tasks/" + pathId(id) + "/cancel", "POST", body),
    creationInputUpdate: (id, query) =>
      request(
        "/admin/document-tasks/" +
          pathId(id) +
          "/input-update/" +
          pathId(query),
      ),
    downloadCreation: (id) =>
      download("/admin/document-tasks/" + pathId(id) + "/draft", "draft.txt"),
    saveCreation: (id, body) =>
      request("/admin/document-tasks/" + pathId(id) + "/save", "POST", body),
    approveCreationSave: (id, sha) =>
      request(
        "/admin/document-tasks/" + pathId(id) + "/save-approval",
        "POST",
        { expected_intent_sha256: sha },
      ),
    queryPolicy: () => request("/admin/query/policy"),
    saveQueryPolicy: (body) => request("/admin/query/policy", "PUT", body),
    queryTasks: () => request("/admin/query/tasks"),
    submitQuery: (body) => request("/admin/query/tasks", "POST", body),
    submitCloudQuery: (body) =>
      request("/admin/query/cloud-tasks", "POST", body),
    launchQueryJob: (body) => request("/admin/query/jobs", "POST", body),
    cancelQueryJob: (id) =>
      request("/admin/query/jobs/" + pathId(id) + "/cancel", "POST"),
    recoverQueryJob: (id) =>
      request("/admin/query/jobs/" + pathId(id) + "/recover", "POST"),
    cancelQueryTask: (role, id) =>
      request(
        "/admin/query/tasks/" + pathId(role) + "/" + pathId(id) + "/cancel",
        "POST",
      ),
    queryDetail: (role, id) =>
      request("/admin/query/tasks/" + pathId(role) + "/" + pathId(id)),
    queryResult: (role, id) =>
      request(
        "/admin/query/tasks/" + pathId(role) + "/" + pathId(id) + "/result",
      ),
    selectQueryBackup: (id, body) =>
      request(
        "/admin/query/tasks/cloud/" + pathId(id) + "/backup",
        "POST",
        body,
      ),
    media: () => request("/admin/media"),
    mediaPacket: (id) =>
      request("/admin/media/" + pathId(id) + "/review-packet"),
    reviewMedia: (id, body) =>
      request("/admin/media/" + pathId(id) + "/review", "POST", body),
    publishMedia: (id, body) =>
      request("/admin/media/" + pathId(id) + "/publish", "POST", body),
    mediaCleanupPlan: (id) =>
      request("/admin/media/" + pathId(id) + "/cleanup-plan", "POST"),
    mediaCleanup: (id, body) =>
      request("/admin/media/" + pathId(id) + "/cleanup", "POST", body),
    documents: () => request("/admin/documents"),
    documentPacket: (id) =>
      request("/admin/documents/" + pathId(id) + "/review-packet"),
    reviewDocument: (id, body) =>
      request("/admin/documents/" + pathId(id) + "/reviews", "POST", body),
    publishDocument: (id) =>
      request("/admin/documents/" + pathId(id) + "/publish", "POST"),
    health: (options = {}) =>
      request("/health", "GET", undefined, record, options),
    session: () => request("/admin/session"),
    login: (password) => request("/admin/login", "POST", { password }),
    logout: () => request("/admin/logout", "POST"),
    vaults: () => request("/admin/vaults", "GET", undefined, vaultList),
    clients: () => request("/admin/clients", "GET", undefined, clientList),
    createClient: (name, create_vault) =>
      request(
        "/admin/clients",
        "POST",
        { name, create_vault },
        (value) =>
          named(value) &&
          typeof value.token === "string" &&
          value.token.length > 0,
      ),
    backups: () => request("/admin/backups", "GET", undefined, backupList),
    prepareBackup: (action, bundle) =>
      request(
        "/admin/backups",
        "POST",
        { action, ...(bundle ? { bundle } : {}) },
        backupItem,
      ),
    revokeClient: (id) =>
      request("/admin/clients/" + pathId(id) + "/revoke", "POST"),
    setPermissions: (client, vault, permissions) =>
      request(
        "/admin/clients/" + pathId(client) + "/vaults/" + pathId(vault),
        "PUT",
        permissions,
      ),
    setDefaultQuery: (id, default_query) =>
      request("/admin/vaults/" + pathId(id), "PATCH", { default_query }),
    approveVault: (id, client_id, permissions) =>
      request("/admin/vaults/" + pathId(id) + "/approve", "POST", {
        client_id,
        ...permissions,
      }),
    pendingBatches: () =>
      request("/admin/batches", "GET", undefined, batchList),
    chooseBatch: (id, action, question) =>
      request("/admin/batches/" + pathId(id) + "/choice", "POST", {
        action,
        ...(question === undefined ? {} : { question }),
      }),
  };
}
