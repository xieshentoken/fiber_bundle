import { temporaryReadFailure } from "./request_errors.js";
// Home derivations only (no DOM, no requests). Every state comes from a recorded read;
// anything not read is reported as unread, never as healthy or zero.
import {
  channelState,
  issues,
  pendingItems,
  readState,
} from "./home_status.js";

export const sourceLabels = {
  batch: "批次",
  documents: "文档",
  media: "媒体",
  videos: "视频处理",
  articles: "文章",
  vault: "空库审批",
  imports: "导入",
  expert: "专家指令",
  automatic: "自动处理",
  channel: "消息入口",
};
const storageDown = (records) =>
  Object.values(records).some(
    (r) => r?.error?.status === 503 && r.error.code === "storage_unavailable",
  );
const value = (record) => (record && !record.error ? record.value : null);

// Overall chip in the page header.
export function homeStatus(records) {
  if (!Object.keys(records).length) return { tone: "info", text: "尚未读取" };
  const found = issues(records);
  if (
    storageDown(records) ||
    (records.backend?.error && !temporaryReadFailure(records.backend.error))
  )
    return { tone: "error", text: "系统异常" };
  if (
    Object.values(records).some((record) => temporaryReadFailure(record?.error))
  )
    return { tone: "waiting", text: "备份正在执行" };
  if (found.length) return { tone: "waiting", text: "需要关注" };
  if (
    [
      "channels",
      "backend",
      "executions",
      "vaults",
      "clients",
      "batches",
      "documents",
      "media",
      "articles",
      "query",
      "source",
      "agents",
      "backups",
      "usage",
    ].some((name) => !records[name])
  )
    return { tone: "info", text: "部分来源尚未读取" };
  return { tone: "success", text: "读取正常" };
}

// Only a failure that blocks many operations earns the banner; details stay in .home-issues.
export function criticalAlert(records) {
  if (storageDown(records)) {
    const sources = Object.entries(records)
      .filter(
        ([, r]) =>
          r?.error?.status === 503 && r?.error?.code !== "backup_snapshot_busy",
      )
      .map(([name]) => name);
    return {
      title: "存储暂不可读 · 管理接口返回 503",
      body: "依赖状态库的读取与提交（导入、审核、设置保存）可能失败；页面保留最近一次成功读取的数据。",
      sources,
      page: "backend",
      action: "查看后台与提醒",
    };
  }
  if (records.backend?.error && !temporaryReadFailure(records.backend.error))
    return {
      title: "后台状态" + readState(records.backend).text,
      body: "无法确认管理后台当前状态；队列与提醒以最近一次成功读取为准。",
      sources: ["backend"],
      page: "backend",
      action: "查看后台与提醒",
    };
  return null;
}

function node(text, tone, page) {
  return { text, tone, page };
}
function unread(record, page) {
  return node(
    readState(record).text,
    temporaryReadFailure(record?.error)
      ? "waiting"
      : record?.error
        ? "error"
        : "idle",
    page,
  );
}

// System map: entry rows and pipeline nodes with an observed state each.
export function architecture(records, todo = pendingItems(records)) {
  const channels = value(records.channels)?.channels || [];
  const entries = channels.slice(0, 3).map((entry) => {
    const state = channelState(entry);
    return {
      label: entry.name,
      text: state.text,
      tone: {
        available: "success",
        configured: "paused",
        needs_action: "waiting",
        unverified: "waiting",
      }[state.state],
      page: "channels",
    };
  });
  if (records.channels?.error)
    entries.push({
      label: "消息入口",
      ...unread(records.channels, "channels"),
    });
  else if (!channels.length)
    entries.push({
      label: "消息入口",
      text: records.channels ? "尚无已登记入口" : "尚未读取",
      tone: "idle",
      page: "channels",
    });
  const source = records.source;
  entries.push({
    label: "文件导入",
    ...(source?.error || !source
      ? unread(source, "imports")
      : node(
          source.value?.value === null
            ? "可归档 · 提取未配置"
            : "提取配置已保存",
          source.value?.value === null ? "idle" : "success",
          "imports",
        )),
  });
  const clients = value(records.clients);
  entries.push({
    label: "GUI / 客户端",
    ...(clients
      ? node(
          `${clients.filter((c) => c.active).length} 个启用身份`,
          "success",
          "clients",
        )
      : unread(records.clients, "clients")),
  });
  const down = storageDown(records);
  const batches = value(records.batches);
  const backend = value(records.backend);
  const query = value(records.query);
  const reviews = todo.filter((t) =>
    ["documents", "media", "articles", "automatic", "videos"].includes(
      t.source,
    ),
  ).length;
  const vaults = value(records.vaults);
  const knownVaults = new Set(homeVaults(vaults || []).map((v) => v.id));
  const index = value(records.index);
  const indexVaults = (index?.vaults || []).filter(
    (v) => vaults === null || knownVaults.has(v.vault_id),
  );
  const backups = value(records.backups);
  const nodes = {
    intake: batches
      ? node(
          batches.filter((b) => b.state === "frozen").length
            ? `${batches.filter((b) => b.state === "frozen").length} 批待确认`
            : batches.some((b) => b.state === "buffering")
              ? "收件缓冲中"
              : "无待确认批次",
          batches.some((b) => b.state === "frozen") ? "waiting" : "success",
          "batches",
        )
      : unread(records.batches, "batches"),
    backend: backend
      ? node(
          backend.managed
            ? "受管实例 · " +
                ({ running: "运行中", stopped: "已停止", recovering: "恢复中" }[
                  backend.state
                ] || backend.state)
            : "外部管理 · 已响应",
          backend.managed && backend.state !== "running" ? "paused" : "success",
          "backend",
        )
      : unread(records.backend, "backend"),
    storage: down
      ? node("存储暂不可读", "error", "backend")
      : Object.values(records).some((r) => r?.value !== undefined && !r.error)
        ? node("最近读取成功", "success", "backend")
        : node("尚未读取", "idle", "backend"),
    model: query
      ? node(
          query.configured ? "模型：已配置，尚未探测" : "模型：尚未配置",
          "info",
          "models",
        )
      : node("模型：尚未确认配置", "idle", "models"),
    review: !["documents", "media", "articles"].every((n) => records[n])
      ? node("尚未完整读取", "idle", "worklist")
      : ["documents", "media", "articles", "automatic", "videos"].some(
            (n) => records[n]?.error,
          )
        ? node("部分任务未能读取", "error", "documents")
        : node(
            reviews ? `${reviews} 项待处理` : "已读取范围无待审核任务",
            reviews ? "waiting" : "success",
            "worklist",
          ),
    vault: vaults
      ? node(
          `${vaults.filter((v) => v.status === "active" && (v.attachment_state ?? "attached") === "attached").length} 个已接入`,
          "success",
          "vaults",
        )
      : unread(records.vaults, "vaults"),
    index: index
      ? node(
          `${indexVaults.filter((v) => v.generation_state === "ready").length}/${indexVaults.length} 库快照就绪`,
          indexVaults.every((v) => v.generation_state === "ready")
            ? "success"
            : "waiting",
          "vaults",
        )
      : records.index
        ? unread(records.index, "vaults")
        : node("轻量摘要尚未接入", "idle", "vaults"),
    backup: backups
      ? node(
          backups.length ? backupText(backups[0].state) : "近期无备份意图记录",
          backups.length && backups[0].state === "backup_verified"
            ? "success"
            : backups.length &&
                ["backup_failed", "backup_cleanup_pending"].includes(
                  backups[0].state,
                )
              ? "error"
              : backups.length &&
                  ["prepared", "backup_running", "backup_waiting"].includes(
                    backups[0].state,
                  )
                ? "waiting"
                : "idle",
          "backups",
        )
      : unread(records.backups, "backups"),
  };
  return { entries, nodes, storageDown: down };
}
function backupText(state) {
  return (
    {
      backup_verified: "加密备份已认证校验",
      backup_failed: "备份失败，请重试",
      backup_running: "备份正在执行",
      backup_waiting: "备份排队等待",
      backup_cleanup_pending: "备份版本清理需核查",
      prepared: "已准备，等待离线执行",
      encrypted_complete: "完成标记已记录，未校验",
      incomplete: "未完成，保留现场",
      failed: "恢复失败",
    }[state] || "最近记录 · " + state
  );
}

// Rich rows for the to-do card; `pendingItems` stays the single source of what is a to-do.
const statusLabels = {
  selection: ["待确认", "waiting"],
  needs_review: ["待审核", "waiting"],
  completed: ["待审核", "waiting"],
  extracted: ["待审核", "waiting"],
  captured: ["待审核", "waiting"],
  complete: ["待审核", "waiting"],
  needs_supplement: ["待补全", "waiting"],
  needs_split: ["待分片", "waiting"],
  paused: ["待补充", "waiting"],
  pending: ["待审批", "waiting"],
  reconcile: ["待核对", "error"],
  trash_pending: ["待核对", "error"],
  failed: ["处理失败", "error"],
  needs_confirmation: ["待确认", "waiting"],
  waiting_budget: ["等待预算", "waiting"],
  interrupted: ["处理中断", "error"],
  unavailable: ["暂不可用", "error"],
};
const platforms = { feishu: "飞书", weixin: "微信" };
export function todoRows(records) {
  const vaultNames = new Map(
    (value(records.vaults) || []).map((v) => [v.id, v.name]),
  );
  const batches = new Map((value(records.batches) || []).map((b) => [b.id, b]));
  return pendingItems(records).map((item) => {
    const recordName =
      { batch: "batches", vault: "vaults", channel: "channels" }[item.source] ||
      item.source;
    const stale = Boolean(records[recordName]?.error);
    const [title, ...rest] = item.text.split(" · ");
    const [status, tone] = item.status
      ? [item.status, item.tone]
      : statusLabels[item.reason] || [rest.at(-1) || "待处理", "waiting"];
    const batch = item.source === "batch" ? batches.get(item.id) : null;
    return {
      ...item,
      systemTitle: Boolean(batch),
      title: batch
        ? `${platforms[batch.platform] || "消息"}批次 · ${batch.event_count ?? batch.count} 条资料`
        : title,
      detailParts: [
        { ui: true, text: sourceLabels[item.source] || item.source },
        {
          text: batch
            ? firstLine(batch.preview)
            : rest.slice(0, -1).join(" · "),
        },
        { ui: true, text: item.source === "automatic" ? rest.at(-1) : null },
        { ui: true, text: item.description },
        { text: vaultNames.get(item.vault_id) },
      ].filter((p) => p.text),
      detail: [
        sourceLabels[item.source] || item.source,
        batch ? firstLine(batch.preview) : null,
        ...rest.slice(0, -1),
        item.source === "automatic" ? rest.at(-1) : null,
        item.description,
        vaultNames.get(item.vault_id),
      ]
        .filter(Boolean)
        .join(" · "),
      status: stale ? "上次记录 · 需刷新" : status,
      tone: stale ? "error" : tone,
    };
  });
}
export function todoGroups(rows) {
  const counts = new Map();
  for (const row of rows)
    counts.set(row.source, (counts.get(row.source) || 0) + 1);
  return [...counts].map(([source, count]) => ({
    source,
    label: sourceLabels[source] || source,
    count,
  }));
}

// Recent intake across batches, scoped imports and article tasks, newest first per source.
const importStates = {
  prepared: ["已归档", "info"],
  processing: ["处理中", "info"],
  needs_review: ["待审核", "waiting"],
  needs_split: ["待分片", "waiting"],
  needs_supplement: ["待补全", "waiting"],
  published: ["已发布", "success"],
  cancelled: ["已取消", "info"],
  failed: ["失败", "error"],
};
export function recentImports(records, now = Date.now()) {
  const rows = [];
  for (const batch of value(records.batches) || []) {
    const waiting = batch.state === "buffering";
    rows.push({
      kind: "batch",
      systemTitle: true,
      title: `${platforms[batch.platform] || "消息"}批次 · ${batch.event_count ?? batch.count} 条资料`,
      detail: firstLine(batch.preview),
      time: batch.received_at,
      status: waiting
        ? remaining(batch.freeze_at, now)
        : batch.state === "frozen"
          ? "待确认"
          : "已选择",
      tone: waiting ? "info" : "waiting",
      page: "batches",
      id: batch.id,
    });
  }
  for (const item of value(records.imports) || []) {
    const [status, tone] = importStates[item.state] || [item.state, "info"];
    rows.push({
      kind: "imports",
      title: item.filename,
      detail: item.expert ? "fake-expert 导入" : "普通资料导入",
      time: null,
      status,
      tone,
      page: "imports",
      id: item.id,
      client_id: item.client_id,
      vault_id: item.vault_id,
    });
  }
  for (const task of value(records.articles)?.tasks || [])
    rows.push({
      kind: "articles",
      title: task.title || task.url || task.run_id,
      detail: "文章",
      time: task.updated_at || task.created_at || null,
      status: task.state === "published" ? "已发布" : "待审核",
      tone: task.state === "published" ? "success" : "waiting",
      page: "documents",
      id: task.run_id,
    });
  return rows;
}
function firstLine(text) {
  const line = String(text || "")
    .split("\n")[0]
    .trim();
  return line.length > 40 ? line.slice(0, 40) + "…" : line;
}
export function remaining(freezeAt, now = Date.now()) {
  if (!Number.isFinite(freezeAt)) return "收集中";
  const minutes = Math.max(0, Math.ceil((freezeAt * 1000 - now) / 60000));
  if (minutes < 1) return "即将冻结";
  const hours = Math.floor(minutes / 60);
  return `约 ${hours ? hours + " 小时 " : ""}${minutes % 60} 分后冻结`;
}

// Removed vaults retain audit history, but are not home destinations.
export function homeVaults(vaults = []) {
  return vaults.filter(
    (v) =>
      ["active", "pending"].includes(v.status) &&
      ["attached", "reconcile", "trash_pending"].includes(
        v.attachment_state || "attached",
      ),
  );
}
export function vaultRows(records) {
  const index = new Map(
    (value(records.index)?.vaults || []).map((v) => [v.vault_id, v]),
  );
  const labels = {
    attached: ["已接入", "success"],
    detached: ["已解除接入", "info"],
    trashed: ["已移到废纸篓", "info"],
    reconcile: ["待核对恢复", "waiting"],
    trash_pending: ["废纸篓操作待完成", "waiting"],
  };
  return homeVaults(records.vaults?.value || []).map((v) => {
    const snapshot = index.get(v.id);
    const [status, tone] =
      v.status === "pending"
        ? ["待审批", "waiting"]
        : labels[v.attachment_state || "attached"] || [
            v.attachment_state,
            "info",
          ];
    return {
      id: v.id,
      name: v.name,
      notes:
        snapshot && Number.isInteger(snapshot.indexed_notes)
          ? `${snapshot.indexed_notes.toLocaleString("en-US")} 篇`
          : null,
      builtAt: snapshot?.built_at ?? null,
      stale: snapshot ? snapshot.generation_state !== "ready" : false,
      status: records.vaults?.error ? "上次记录 · 需刷新" : status,
      tone: records.vaults?.error ? "error" : tone,
    };
  });
}

export { costSummary, costWindow } from "./home_usage_model.js";

const accessLabels = {
  enabled: ["已授权", "success"],
  paused: ["已暂停", "info"],
  pending: ["待启用", "waiting"],
  revoked: ["已撤销", "info"],
  repair_required: ["需修复", "error"],
};
const testLabels = {
  passed: "最近测试通过",
  failed: "最近测试失败",
  outdated: "测试结果已过期",
  not_run: "尚未测试",
};
export function agentRows(records) {
  return (records.agents?.value || []).map((agent) => {
    const [status, tone] = accessLabels[agent.access_state] || [
      agent.access_state || "状态未记录",
      "info",
    ];
    return {
      id: agent.id,
      name: agent.configuration?.name || agent.id,
      detail: [
        agent.configuration?.agent_type === "hermes" ? "Hermes" : "MCP",
        testLabels[agent.test?.state] || "尚未测试",
      ].join(" · "),
      status,
      tone,
    };
  });
}
