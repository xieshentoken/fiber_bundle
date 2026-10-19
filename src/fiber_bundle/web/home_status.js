// Evidence rules only. A missing response is never a zero or a health failure.
import { automaticStatus } from "./automatic_status.js";
export function timeText(value) {
  if (!Number.isFinite(value) || value <= 0) return "未记录";
  return (
    new Intl.DateTimeFormat("zh-CN", {
      dateStyle: "short",
      timeStyle: "medium",
      timeZone: "Asia/Taipei",
    }).format(new Date(value < 1e12 ? value * 1000 : value)) + " UTC+8"
  );
}
export function shortTime(value) {
  if (!Number.isFinite(value) || value <= 0) return "未记录";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone: "Asia/Taipei",
    })
      .formatToParts(new Date(value < 1e12 ? value * 1000 : value))
      .map((part) => [part.type, part.value]),
  );
  return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
export function readState(record) {
  if (!record) return { state: "unrecorded", text: "尚未读取" };
  if (record.error?.code === "backup_snapshot_busy")
    return { state: "unverified", text: "备份正在执行" };
  if (record.error)
    return {
      state: "unverified",
      text:
        {
          401: "会话已失效",
          403: "读取未获授权",
          404: "资源或范围不存在",
          503: "存储暂不可读",
        }[record.error.status] ||
        (record.error.code === "timeout" ? "读取超时" : "本次无法刷新"),
    };
  return { state: "available", text: "已读取" };
}
export function channelState(entry) {
  if (!entry) return { state: "unrecorded", text: "未记录" };
  if (entry.unresolved_deliveries > 0)
    return { state: "needs_action", text: "送达结果待核对" };
  if (["stopped", "paused"].includes(entry.state))
    return { state: "configured", text: "入口已暂停" };
  if (entry.binding_state !== "bound")
    return { state: "needs_action", text: "等待绑定确认" };

  if (entry.runtime?.heartbeat_fresh !== true)
    return { state: "unverified", text: "心跳未确认或已过期" };
  if (entry.transport === "connected")
    return { state: "available", text: "传输已连接" };
  return { state: "unverified", text: "传输尚未确认" };
}
export function issues(records) {
  const unique = new Map();
  for (const [source, record] of Object.entries(records)) {
    if (!record?.error) continue;
    const storage =
      record.error.status === 503 &&
      record.error.code === "storage_unavailable";
    const snapshot = record.error.code === "backup_snapshot_busy";
    const key = snapshot
      ? "backup_snapshot_busy"
      : storage
        ? "storage_unavailable"
        : `${source}:${record.error.status}:${record.error.code}`;
    if (!unique.has(key))
      unique.set(key, {
        key,
        sources: [],
        text: readState(record).text,
        page: snapshot ? "backups" : storage ? "backend" : "home",
      });
    unique.get(key).sources.push(source);
  }
  for (const entry of records.channels?.value?.channels || []) {
    const state = channelState(entry);
    if (!records.channels.error && state.state === "unverified")
      unique.set(`channel:${entry.id}`, {
        key: `channel:${entry.id}`,
        sources: ["channels"],
        text: `${entry.name} · ${state.text}`,
        page: "channels",
      });
  }
  return [...unique.values()];
}
export function pendingItems(records) {
  const result = [],
    seen = new Set();
  function add(source, id, text, page, reason, owner = {}) {
    const key = `${source}:${id}:${reason}`;
    if (seen.has(key)) return;
    seen.add(key);
    result.push({
      key,
      source,
      id,
      text: records[source]?.error ? `上次读取 · ${text}（需刷新）` : text,
      page,
      reason,
      ...owner,
    });
  }
  for (const v of records.vaults?.value || [])
    if (["pending", "reconcile", "trash_pending"].includes(v.status))
      add("vault", v.id, v.name + " · 待审批或核对", "pending", v.status);
  for (const b of records.batches?.value || [])
    if (b.state === "frozen")
      add("batch", b.id, b.preview || "批次待选择", "batches", "selection");
  for (const [source, page] of [
    ["documents", "documents"],
    ["media", "media"],
    ["articles", "documents"],
    ["imports", "imports"],
  ]) {
    const data = records[source]?.value,
      rows = Array.isArray(data) ? data : data?.tasks || [];
    for (const item of rows) {
      const state = item.expert?.state || item.state || item.status;
      if (
        item.publication?.state === "published" ||
        item.publication_state === "published" ||
        state === "published"
      )
        continue;
      if (
        ["queued", "claimed", "waiting", "reviewed"].includes(
          item.automatic_state || item.automatic?.state,
        )
      )
        continue;
      const candidate =
        ["extracted", "captured", "complete"].includes(state) &&
        Object.hasOwn(item, "publication_state") &&
        (source === "media" || item.run_state === "succeeded");
      if (
        candidate ||
        [
          "needs_review",
          "needs_supplement",
          "needs_split",
          "paused",
          "completed",
        ].includes(state)
      )
        add(
          source,
          item.id || item.run_id || item.job_id,
          `${item.filename || item.title || item.id || item.run_id || item.job_id} · ${state === "paused" ? "待补充" : state === "needs_supplement" ? "待补全" : "待审核或发布"}`,
          page,
          state,
          { client_id: item.client_id, vault_id: item.vault_id },
        );
    }
  }
  for (const c of records.channels?.value?.channels || []) {
    const state = channelState(c);
    if (state.state === "needs_action")
      add("channel", c.id, `${c.name} · ${state.text}`, "channels", state.text);
  }
  for (const command of records.expert?.value || [])
    if (
      [
        "waiting_file",
        "needs_selection",
        "awaiting_download",
        "expired",
        "reconcile",
        "paused",
      ].includes(command.state)
    )
      add(
        "expert",
        command.event_id,
        `专家指令 · ${command.reason || command.state}`,
        "imports",
        command.state,
      );
  const jobs = records.automatic?.value;
  for (const video of records.videos?.value || [])
    if (
      [
        "needs_confirmation",
        "failed",
        "waiting_budget",
        "interrupted",
        "unavailable",
      ].includes(video.state)
    )
      add(
        "videos",
        video.id,
        `视频提取 · ${video.error_message || video.reason || video.state}`,
        "media",
        video.state,
        { client_id: video.client_id, vault_id: video.vault_id },
      );
  for (const j of Array.isArray(jobs) ? jobs : jobs?.jobs || []) {
    const status = automaticStatus(j);
    if (status.actionRequired)
      add(
        "automatic",
        j.id,
        `${j.title || { document: "文档提取", article: "文章提取", media: "媒体提取", chat: "聊天归档", intake: "链接提取", expert: "专家提取", intake_summary: "附属摘要" }[j.kind] || "自动处理"}`,
        "vaults",
        status.state,
        {
          vault_id: j.vault_id,
          client_id: j.client_id,
          status: status.text,
          tone: status.tone,
          description: j.display_message || j.reason || status.text,
        },
      );
  }
  return result;
}
