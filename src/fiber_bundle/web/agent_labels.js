// Human-readable projections; backend codes and lifecycle contracts stay unchanged.
export function operationTitle(kind) {
  return (
    {
      create: "创建连接",
      apply_config: "应用接入配置",
      probe: "验证连接（无模型）",
      test_query: "问答测试",
      rotate_credential: "轮换凭证",
      cancel: "取消受管操作",
    }[kind] || "受管操作"
  );
}
export function operationState(state) {
  return (
    {
      queued: "已排队",
      running: "正在执行",
      succeeded: "已完成",
      failed: "未完成",
      cancelled: "已取消",
      interrupted: "已中断",
    }[state] || "状态待核对"
  );
}
export function agentErrorText(code) {
  return {
    agent_paused: "连接已暂停。核对所选知识库后，可明确授权并启用查询。",
    agent_revoked: "连接已永久撤销。需要继续使用时，请新建连接。",
    agent_apply_required: "连接配置尚未应用。请先应用当前配置，再授权查询。",
    agent_revision_conflict:
      "连接已在其他窗口修改。当前编辑已保留，请核对最新配置后再保存。",
    agent_operation_busy:
      "此连接已有操作在执行。请等待完成，或明确取消该操作。",
    agent_request_conflict: "同一请求编号的内容发生变化，请核对后重新提交。",
    agent_credential_changed: "凭证已变化，请显式轮换并重新应用配置。",
    agent_managed_test_unavailable: "通用 MCP 的问答由外部宿主负责。",
    agent_connection_limit: "已达到 50 个连接的限额。",
    agent_cloud_budget_exhausted:
      "云端次数或费用额度已用尽。请核对模型页的额度，系统不会切换备用。",
    agent_daily_budget_exhausted:
      "此连接今日测试次数已用尽。请稍后再显式提交。",
    agent_index_stale: "知识库索引已过期。请在知识库页显式更新索引后再测试。",
    agent_source_changed:
      "来源内容已变化，旧答案不再交付。请核对资料后重新测试。",
    agent_evidence_changed:
      "来源或访问权限已变化，本次答案不再交付。请核对当前资料与范围。",
    agent_generation_failed:
      "本次生成未完成。请查看模型服务错误；用量未知时不会自动重试。",
    agent_source_stale: "来源已过期。请核对当前资料后重新测试。",
    agent_context_changed:
      "权限、配置或来源已变化，旧结果不再交付。请核对当前范围。",
    agent_permissions_changed:
      "连接权限已变化。请核对当前知识库范围，不能沿用旧授权。",
    agent_model_unconfigured:
      "所选模型配置不可用。请在模型页完成配置后再明确选择。",
    agent_model_credential_invalid:
      "模型凭据不可用。请核对模型页配置的私有凭据。",
    agent_timeout: "操作已达到硬截止。请核对结果；用量未知时不会自动重试。",
    agent_cancelled: "操作已取消，不交付本次回答。",
    agent_hermes_incompatible:
      "Hermes 版本或依赖不兼容。请核对本机安装，或选择通用 MCP。",
    agent_provider_http_507:
      "模型服务报告资源不足。请核对服务状态后再显式提交。",
  }[code];
}
export function agentError(code) {
  if (!/^agent_[a-z_0-9]{1,100}$/.test(code)) return code;
  const message = agentErrorText(code);
  if (message) return `${message}（${code}）`;
  if (code.startsWith("agent_provider_"))
    return `模型服务未完成请求。本次不会自动重试，请先核对服务状态。（${code}）`;
  return `操作未完成。请核对当前配置和错误详情。（${code}）`;
}
