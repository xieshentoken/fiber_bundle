// Read-only independent-role observations. No optimistic health or control operations.
export function roleObservation(role, stale = false) {
  if (stale) return { text: "状态已过期，待刷新", tone: "waiting" };
  if (!role || !role.managed) return { text: "尚无独立服务状态", tone: "idle" };
  if (!role.enabled) return { text: "已暂停", tone: "paused" };
  if (!role.process_running || !role.heartbeat_fresh)
    return { text: "心跳未确认或已过期", tone: "waiting" };
  return (
    {
      idle: { text: "运行中", tone: "success" },
      working: { text: "正在处理", tone: "success" },
      starting: { text: "启动中", tone: "waiting" },
      draining: { text: "正在结束当前阶段", tone: "waiting" },
      waiting_storage: { text: "等待存储", tone: "waiting" },
      blocked: { text: "处理需核对", tone: "waiting" },
    }[role.state] || { text: "状态尚未确认", tone: "waiting" }
  );
}
export function receiptObservation(value, stale = false) {
  if (stale) return { text: "状态已过期，待刷新", tone: "waiting" };
  if (!value) return { text: "尚未读取", tone: "idle" };
  const held = (value.needs_review_batches || 0) + (value.sending_batches || 0);
  if (held) return { text: `${held} 批回执待核对`, tone: "waiting" };
  if (!value.enabled) return { text: "新回执已关闭", tone: "paused" };
  return {
    text: value.pending_batches
      ? `${value.pending_batches} 批回执待发送或更新`
      : "无待发送回执",
    tone: value.pending_batches ? "info" : "success",
  };
}
