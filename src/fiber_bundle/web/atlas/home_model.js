import { temporaryReadFailure } from "../request_errors.js";
import { visibleVault } from "./vault_visibility.js";
// Home and inbox derivations for Atlas (no DOM, no requests). What counts as a to-do
// stays defined by the shared ../home_status.js; this module only words it.
import { translate } from "../i18n/index.js";
import { pendingItems, readState } from "../home_status.js";
import { strings } from "./i18n.js";
import { firstLine } from "./format.js";

export const t = strings({
  zh: {
    src_batch: "消息批次",
    src_documents: "文档",
    src_media: "媒体",
    src_videos: "视频提取",
    needs_confirmation: "待确认",
    waiting_budget: "等待预算",
    interrupted: "处理中断",
    unavailable: "暂不可用",
    src_articles: "文章",
    src_vault: "空库审批",
    src_imports: "导入",
    src_expert: "专家指令",
    src_automatic: "自动处理",
    src_channel: "消息入口",
    selection: "待选择",
    needs_review: "待审核",
    needs_supplement: "待补全",
    needs_split: "待分片",
    paused: "待补充",
    pending: "待审批",
    reconcile: "待核对",
    trash_pending: "待核对",
    failed: "处理失败",
    waiting_file: "等待文件",
    needs_selection: "待选择文件",
    awaiting_download: "等待下载",
    expired: "已过期",
    review: "待审核或发布",
    batchTitle: "{platform}批次 · {n} 条资料",
    feishu: "飞书",
    weixin: "微信",
    message: "消息",
    expertCommand: "专家指令",
    automaticJob: "自动处理",
    ch_unrecorded: "未记录",
    ch_deliveries: "送达结果待核对",
    ch_paused: "入口已暂停",
    ch_binding: "等待绑定确认",
    ch_heartbeat: "心跳未确认或已过期",
    ch_connected: "传输已连接",
    ch_transport: "传输尚未确认",
    rd_unread: "尚未读取",
    rd_401: "会话已失效",
    rd_403: "读取未获授权",
    rd_404: "资源或范围不存在",
    rd_503: "存储暂不可读",
    rd_timeout: "读取超时",
    rd_failed: "本次无法刷新",
    rd_ok: "已读取",
    h_intake: "消息收件",
    h_backend: "后台",
    h_storage: "状态库",
    h_model: "模型",
    h_review: "审核",
    h_vault: "知识库",
    h_index: "索引快照",
    h_backup: "备份",
    h_source: "文件提取",
    h_clients: "客户端",
    frozenBatches: "{n} 批待确认",
    buffering: "收件缓冲中",
    noBatches: "无待确认批次",
    managed: "受管实例 · {state}",
    external: "外部管理 · 已响应",
    storageDown: "暂不可读",
    storageOk: "最近读取成功",
    modelConfigured: "已配置，尚未探测",
    modelMissing: "尚未配置",
    reviewPartial: "部分任务未能读取",
    reviews: "{n} 项待审核",
    noReviews: "无待审核任务",
    attached: "{n} 个已接入",
    snapshots: "{ready}/{total} 库快照就绪",
    indexMissing: "轻量摘要尚未接入",
    noBackup: "近期无备份记录",
    bk_prepared: "已准备，等待离线执行",
    bk_encrypted_complete: "完成标记已记录，未校验",
    bk_incomplete: "未完成，保留现场",
    bk_failed: "恢复失败",
    bk_other: "最近记录 · {state}",
    sourceNone: "可归档 · 提取未配置",
    sourceSet: "提取配置已保存",
    activeClients: "{n} 个启用身份",
    v_attached: "已接入",
    v_detached: "已解除接入",
    v_trashed: "已移到废纸篓",
    v_reconcile: "待核对恢复",
    v_trash_pending: "废纸篓操作待完成",
    v_pending: "待审批",
    notes: "{n} 篇",
    a_enabled: "已授权",
    a_paused: "已暂停",
    a_pending: "待启用",
    a_revoked: "已撤销",
    a_repair_required: "需修复",
    test_passed: "最近测试通过",
    test_failed: "最近测试失败",
    test_outdated: "测试结果已过期",
    test_not_run: "尚未测试",
    i_prepared: "已归档",
    i_processing: "处理中",
    i_published: "已发布",
    i_cancelled: "已取消",
    collecting: "收集中",
    freezingSoon: "即将冻结",
    freezesIn: "约 {m} 分后冻结",
    selected: "已选择",
    ordinaryImport: "普通资料导入",
    expertImport: "fake-expert 导入",
    article: "文章",
    alertStorage: "存储暂不可读 · 管理接口返回 503",
    alertStorageBody:
      "依赖状态库的读取与提交（导入、审核、设置保存）可能失败；页面保留最近一次成功读取的数据。",
    alertBackend: "后台状态：{state}",
    alertBackendBody:
      "无法确认管理后台当前状态；队列与提醒以最近一次成功读取为准。",
  },
  en: {
    src_batch: "Message batch",
    src_documents: "Document",
    src_media: "Media",
    src_videos: "Video extraction",
    needs_confirmation: "Confirm",
    waiting_budget: "Waiting for budget",
    interrupted: "Interrupted",
    unavailable: "Unavailable",
    src_articles: "Article",
    src_vault: "Vault approval",
    src_imports: "Import",
    src_expert: "Expert command",
    src_automatic: "Automation",
    src_channel: "Channel",
    selection: "Choose action",
    needs_review: "Review",
    needs_supplement: "Needs completion",
    needs_split: "Needs split",
    paused: "Needs input",
    pending: "Approve",
    reconcile: "Reconcile",
    trash_pending: "Reconcile",
    failed: "Failed",
    waiting_file: "Waiting for file",
    needs_selection: "Choose file",
    awaiting_download: "Downloading",
    expired: "Expired",
    review: "Review or publish",
    batchTitle: "{platform} batch · {n} items",
    feishu: "Feishu",
    weixin: "WeChat",
    message: "Message",
    expertCommand: "Expert command",
    automaticJob: "Automatic processing",
    ch_unrecorded: "Not recorded",
    ch_deliveries: "Delivery results to reconcile",
    ch_paused: "Channel paused",
    ch_binding: "Waiting for binding confirmation",
    ch_heartbeat: "Heartbeat unconfirmed or stale",
    ch_connected: "Transport connected",
    ch_transport: "Transport not yet confirmed",
    rd_unread: "Not read yet",
    rd_401: "Session expired",
    rd_403: "Read not permitted",
    rd_404: "Resource or scope missing",
    rd_503: "Storage unreadable",
    rd_timeout: "Read timed out",
    rd_failed: "Couldn't refresh",
    rd_ok: "Read",
    h_intake: "Message intake",
    h_backend: "Backend",
    h_storage: "State store",
    h_model: "Models",
    h_review: "Review",
    h_vault: "Vaults",
    h_index: "Index snapshots",
    h_backup: "Backups",
    h_source: "File extraction",
    h_clients: "Clients",
    frozenBatches: "{n} batches to confirm",
    buffering: "Buffering messages",
    noBatches: "No batches to confirm",
    managed: "Managed instance · {state}",
    external: "Externally managed · responding",
    storageDown: "Unreadable",
    storageOk: "Last reads succeeded",
    modelConfigured: "Configured, not probed",
    modelMissing: "Not configured",
    reviewPartial: "Some tasks unreadable",
    reviews: "{n} to review",
    noReviews: "Nothing to review",
    attached: "{n} connected",
    snapshots: "{ready}/{total} snapshots ready",
    indexMissing: "Summary not available",
    noBackup: "No recent backup records",
    bk_prepared: "Prepared, awaiting offline run",
    bk_encrypted_complete: "Completion marker recorded, unverified",
    bk_incomplete: "Incomplete, state kept",
    bk_failed: "Restore failed",
    bk_other: "Latest record · {state}",
    sourceNone: "Archive only · extraction not configured",
    sourceSet: "Extraction configured",
    activeClients: "{n} active identities",
    v_attached: "Connected",
    v_detached: "Detached",
    v_trashed: "Moved to Trash",
    v_reconcile: "Reconcile restore",
    v_trash_pending: "Trash pending",
    v_pending: "Awaiting approval",
    notes: "{n} notes",
    a_enabled: "Authorized",
    a_paused: "Paused",
    a_pending: "Pending",
    a_revoked: "Revoked",
    a_repair_required: "Needs repair",
    test_passed: "Last test passed",
    test_failed: "Last test failed",
    test_outdated: "Test outdated",
    test_not_run: "Not tested",
    i_prepared: "Archived",
    i_processing: "Processing",
    i_published: "Published",
    i_cancelled: "Cancelled",
    collecting: "Collecting",
    freezingSoon: "Freezing soon",
    freezesIn: "Freezes in ~{m} min",
    selected: "Selected",
    ordinaryImport: "Regular import",
    expertImport: "fake-expert import",
    article: "Article",
    alertStorage: "Storage unreadable · admin API returned 503",
    alertStorageBody:
      "Reads and submissions that need the state store (imports, reviews, saving settings) may fail. The page keeps the last successful data.",
    alertBackend: "Backend status: {state}",
    alertBackendBody:
      "The admin backend's state can't be confirmed; queues and reminders reflect the last successful read.",
  },
});

const value = (record) => (record && !record.error ? record.value : null);
export const storageDown = (records) =>
  Object.values(records).some(
    (r) => r?.error?.status === 503 && r.error.code === "storage_unavailable",
  );

export function readText(record) {
  if (!record) return t("rd_unread");
  if (!record.error) return t("rd_ok");
  if (temporaryReadFailure(record.error))
    return translate(readState(record).text);
  const status = record.error.status;
  if ([401, 403, 404, 503].includes(status)) return t("rd_" + status);
  return record.error.code === "timeout" ? t("rd_timeout") : t("rd_failed");
}
// Same evidence rules as the shared channelState, expressed as codes.
export function channelState(entry) {
  if (!entry) return { code: "unrecorded", tone: "idle" };
  if (entry.unresolved_deliveries > 0)
    return { code: "deliveries", tone: "waiting", action: true };
  if (["stopped", "paused"].includes(entry.state))
    return { code: "paused", tone: "paused" };
  if (entry.binding_state !== "bound")
    return { code: "binding", tone: "waiting", action: true };
  if (entry.runtime?.heartbeat_fresh !== true)
    return { code: "heartbeat", tone: "waiting" };
  if (entry.transport === "connected")
    return { code: "connected", tone: "success" };
  return { code: "transport", tone: "waiting" };
}
export const channelText = (entry) => t("ch_" + channelState(entry).code);

const tones = { reconcile: "error", trash_pending: "error", failed: "error" };
const platformName = (platform) =>
  platform === "feishu" || platform === "weixin" ? t(platform) : t("message");
export function batchTitle(batch) {
  return t("batchTitle", {
    platform: platformName(batch.platform),
    n: batch.event_count ?? batch.count,
  });
}
// Inbox rows: one per shared to-do item, with bilingual wording.
export function todoRows(records) {
  const vaultNames = new Map(
    (value(records.vaults) || []).map((v) => [v.id, v.name]),
  );
  const batches = new Map((value(records.batches) || []).map((b) => [b.id, b]));
  const automatic = value(records.automatic);
  const jobs = new Map(
    (Array.isArray(automatic) ? automatic : automatic?.jobs || []).map((j) => [
      j.id,
      j,
    ]),
  );
  return pendingItems(records).map((item) => {
    const name = item.text.replace(/^上次读取 · /, "").split(" · ")[0];
    let title = name,
      detail = "";
    if (item.source === "batch") {
      const batch = batches.get(item.id);
      title = batch ? batchTitle(batch) : t("src_batch");
      detail = batch ? firstLine(batch.preview, 48) : "";
    } else if (item.source === "expert") title = t("expertCommand");
    else if (item.source === "automatic")
      title = jobs.get(item.id)?.title || t("automaticJob");
    const reasonKey = [
      "completed",
      "extracted",
      "captured",
      "complete",
    ].includes(item.reason)
      ? "needs_review"
      : item.reason;
    let status = item.status ? translate(item.status) : t(reasonKey);
    let tone = item.tone || tones[item.reason] || "waiting";
    if (status === reasonKey)
      status =
        item.source === "channel"
          ? channelText(channelEntry(records, item.id))
          : t("review");
    return {
      ...item,
      title,
      detail: [
        detail,
        item.description ? translate(item.description) : "",
        vaultNames.get(item.vault_id),
      ]
        .filter(Boolean)
        .join(" · "),
      sourceLabel: t("src_" + item.source),
      status: [status, tone],
      stale: Boolean(records[sourceRecord(item.source)]?.error),
    };
  });
}
function channelEntry(records, id) {
  return (value(records.channels)?.channels || []).find((c) => c.id === id);
}
function sourceRecord(source) {
  return (
    { batch: "batches", vault: "vaults", channel: "channels" }[source] || source
  );
}
export function todoGroups(rows) {
  const counts = new Map();
  for (const row of rows)
    counts.set(row.source, (counts.get(row.source) || 0) + 1);
  return [...counts].map(([source, count]) => ({
    source,
    label: t("src_" + source),
    count,
  }));
}

// System health list: each line states only what the latest read showed.
export function health(records, todo = pendingItems(records)) {
  const lines = [];
  const unread = (key, record, page) =>
    lines.push({
      key,
      label: t("h_" + key),
      text: readText(record),
      tone: record?.error ? "error" : "idle",
      page,
    });
  const batches = value(records.batches);
  if (batches) {
    const frozen = batches.filter((b) => b.state === "frozen").length;
    lines.push({
      key: "intake",
      label: t("h_intake"),
      text: frozen
        ? t("frozenBatches", { n: frozen })
        : batches.some((b) => b.state === "buffering")
          ? t("buffering")
          : t("noBatches"),
      tone: frozen ? "waiting" : "success",
      page: "worklist",
      sub: "batches",
    });
  } else unread("intake", records.batches, "worklist");
  const backend = value(records.backend);
  if (backend)
    lines.push({
      key: "backend",
      label: t("h_backend"),
      text: backend.managed
        ? t("managed", { state: backend.state })
        : t("external"),
      tone:
        backend.managed && backend.state !== "running" ? "paused" : "success",
      page: "backend",
    });
  else unread("backend", records.backend, "backend");
  const down = storageDown(records);
  lines.push({
    key: "storage",
    label: t("h_storage"),
    text: down
      ? t("storageDown")
      : Object.values(records).some((r) => r?.value !== undefined && !r.error)
        ? t("storageOk")
        : t("rd_unread"),
    tone: down ? "error" : "success",
    page: "backend",
  });
  const query = value(records.query);
  lines.push({
    key: "model",
    label: t("h_model"),
    text: query
      ? query.configured
        ? t("modelConfigured")
        : t("modelMissing")
      : readText(records.query),
    tone: query?.configured ? "success" : "idle",
    page: "models",
  });
  const reviews = todo.filter((item) =>
    ["documents", "media", "articles"].includes(item.source),
  ).length;
  const reviewFailed = ["documents", "media", "articles"].some(
    (name) => records[name]?.error,
  );
  lines.push({
    key: "review",
    label: t("h_review"),
    text: reviewFailed
      ? t("reviewPartial")
      : reviews
        ? t("reviews", { n: reviews })
        : t("noReviews"),
    tone: reviewFailed ? "error" : reviews ? "waiting" : "success",
    page: "worklist",
  });
  const vaults = value(records.vaults);
  if (vaults)
    lines.push({
      key: "vault",
      label: t("h_vault"),
      text: t("attached", {
        n: vaults.filter(
          (v) =>
            v.status === "active" &&
            (v.attachment_state ?? "attached") === "attached",
        ).length,
      }),
      tone: "success",
      page: "vaults",
    });
  else unread("vault", records.vaults, "vaults");
  const index = value(records.index);
  if (index)
    lines.push({
      key: "index",
      label: t("h_index"),
      text: t("snapshots", {
        ready: index.vaults.filter((v) => v.generation_state === "ready")
          .length,
        total: index.vaults.length,
      }),
      tone: index.vaults.every((v) => v.generation_state === "ready")
        ? "success"
        : "waiting",
      page: "vaults",
    });
  else if (records.index) unread("index", records.index, "vaults");
  const source = records.source;
  if (source && !source.error)
    lines.push({
      key: "source",
      label: t("h_source"),
      text: source.value?.value === null ? t("sourceNone") : t("sourceSet"),
      tone: source.value?.value === null ? "idle" : "success",
      page: "inventory",
    });
  const backups = value(records.backups);
  if (backups)
    lines.push({
      key: "backup",
      label: t("h_backup"),
      text: backups.length ? backupText(backups[0].state) : t("noBackup"),
      tone:
        backups.length && backups[0].state === "prepared" ? "waiting" : "idle",
      page: "backups",
    });
  else unread("backup", records.backups, "backups");
  return lines;
}
export function backupText(state) {
  return ["prepared", "encrypted_complete", "incomplete", "failed"].includes(
    state,
  )
    ? t("bk_" + state)
    : t("bk_other", { state });
}
export function criticalAlert(records) {
  if (storageDown(records))
    return {
      title: t("alertStorage"),
      body: t("alertStorageBody"),
      page: "backend",
    };
  if (records.backend?.error && !temporaryReadFailure(records.backend.error))
    return {
      title: t("alertBackend", { state: readText(records.backend) }),
      body: t("alertBackendBody"),
      page: "backend",
    };
  return null;
}
export function vaultRows(records) {
  const index = new Map(
    (value(records.index)?.vaults || []).map((v) => [v.vault_id, v]),
  );
  const tonesByState = {
    attached: "success",
    detached: "info",
    trashed: "info",
    reconcile: "waiting",
    trash_pending: "waiting",
  };
  return (value(records.vaults) || [])
    .filter(visibleVault)
    .filter((v) => v.status !== "rejected")
    .map((v) => {
      const snapshot = index.get(v.id);
      const state =
        v.status === "pending" ? "pending" : v.attachment_state || "attached";
      return {
        id: v.id,
        name: v.name,
        defaultQuery: !!v.default_query,
        notes:
          snapshot && Number.isInteger(snapshot.indexed_notes)
            ? t("notes", { n: snapshot.indexed_notes.toLocaleString() })
            : null,
        builtAt: snapshot?.built_at ?? null,
        stale: snapshot ? snapshot.generation_state !== "ready" : false,
        status: [
          t("v_" + state) === "v_" + state ? state : t("v_" + state),
          v.status === "pending" ? "waiting" : tonesByState[state] || "info",
        ],
      };
    });
}
export function agentRows(records) {
  const tonesByState = {
    enabled: "success",
    paused: "paused",
    pending: "waiting",
    revoked: "info",
    repair_required: "error",
  };
  return (value(records.agents) || []).map((agent) => ({
    id: agent.id,
    name: agent.configuration?.name || agent.id,
    detail: [
      agent.configuration?.agent_type === "hermes" ? "Hermes" : "MCP",
      t("test_" + (agent.test?.state || "not_run")),
    ].join(" · "),
    status: [
      tonesByState[agent.access_state]
        ? t("a_" + agent.access_state)
        : agent.access_state || "—",
      tonesByState[agent.access_state] || "info",
    ],
  }));
}
export function remaining(freezeAt, now = Date.now()) {
  if (!Number.isFinite(freezeAt)) return t("collecting");
  const minutes = Math.max(0, Math.ceil((freezeAt * 1000 - now) / 60000));
  return minutes < 1 ? t("freezingSoon") : t("freezesIn", { m: minutes });
}
export function recentImports(records, now = Date.now()) {
  const rows = [];
  for (const batch of value(records.batches) || [])
    rows.push({
      kind: "batch",
      iconName: "batch",
      title: batchTitle(batch),
      detail: firstLine(batch.preview, 48),
      status:
        batch.state === "buffering"
          ? [remaining(batch.freeze_at, now), "info"]
          : [
              batch.state === "frozen" ? t("selection") : t("selected"),
              "waiting",
            ],
      page: "worklist",
      sub: "batches",
    });
  const importTones = {
    prepared: "info",
    processing: "info",
    published: "success",
    cancelled: "info",
    failed: "error",
  };
  for (const item of value(records.imports) || [])
    rows.push({
      kind: "imports",
      iconName: item.expert ? "expert" : "file",
      title: item.filename,
      detail: item.expert ? t("expertImport") : t("ordinaryImport"),
      status: [
        importTones[item.state]
          ? t(item.state === "failed" ? "failed" : "i_" + item.state)
          : t(item.state) || item.state,
        importTones[item.state] || "waiting",
      ],
      page: "imports",
      params: { client: item.client_id, vault: item.vault_id },
    });
  for (const task of value(records.articles)?.tasks || [])
    rows.push({
      kind: "articles",
      iconName: "article",
      title: task.title || task.url || task.run_id,
      detail: t("article"),
      status:
        task.state === "published"
          ? [t("i_published"), "success"]
          : [t("needs_review"), "waiting"],
      page: "worklist",
      sub: "documents",
    });
  return rows;
}
