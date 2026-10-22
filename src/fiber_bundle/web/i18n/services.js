export default {
  "channel.receipt.sending": [
    "{p0} 批回执发送待确认，不会重复发送。",
    "{p0} batches await send confirmation; no duplicate send will be attempted.",
  ],
  "channel.transport.title": ["平台通道", "Platform transport"],
  "channel.query.title": ["查询服务", "Query service"],
  "channel.model.missing": [
    "尚未配置默认查询模型",
    "No default query model configured",
  ],
  "channel.model.disabled": [
    "默认查询模型或凭据已停用",
    "Default query model or credential disabled",
  ],
  "channel.processing.title": ["资料处理：{p0}", "Processing: {p0}"],
  "channel.receipt.stale": [
    "回执状态已过期，请刷新。",
    "Receipt status is stale. Refresh to check.",
  ],
  "channel.receipt.on": ["回执已开启。", "Receipts enabled."],
  "channel.receipt.off": ["新回执已关闭。", "New receipts disabled."],
  "channel.receipt.held": [
    "{p0} 批回执待核对，不会自动重发。",
    "{p0} batches need receipt reconciliation; they will not be resent automatically.",
  ],
  "channel.receipt.pending": [
    "{p0} 批回执待发送或更新。",
    "{p0} batches await a receipt or status update.",
  ],
  "channel.receipt.independent": [
    "新批次回执独立发送。",
    "New batches send receipts independently.",
  ],
  "channel.receipt.disconnected": [
    "平台连接恢复后继续发送。",
    "Sending resumes when the platform reconnects.",
  ],
  "services.error.pause.config": [
    "修改入口配置前，请先分别暂停查询和保存服务并等待当前阶段结束。",
    "Before editing entry settings, pause its query and saving services and wait for the current stage to finish.",
  ],
  "services.purpose": ["入口职责", "Entry purpose"],
  "services.query": ["知识库查询", "Knowledge queries"],
  "services.intake": ["笔记保存", "Save notes"],
  "services.both": ["兼用（现有配置）", "Combined (existing configuration)"],
  "services.group.both": [
    "兼用入口（现有配置）",
    "Combined entries (existing configuration)",
  ],
  "services.group.query": ["知识库查询入口", "Knowledge query entries"],
  "services.group.intake": ["笔记保存入口", "Note saving entries"],
  "services.heading": ["独立运行", "Independent services"],
  "services.hint": [
    "查询、接收与保存分别控制。暂停处理不会断开机器人连接。",
    "Queries, reception, and saving have separate controls. Pausing processing keeps the bot connected.",
  ],
  "services.enable": [
    "启用独立运行（保留历史任务）",
    "Enable independent services (keep historical tasks)",
  ],
  "services.legacy": ["尚未启用独立运行", "Independent services not enabled"],
  "services.idle": ["运行中", "Running"],
  "services.working": ["正在处理", "Processing"],
  "services.starting": ["启动中", "Starting"],
  "services.draining": ["正在结束当前阶段", "Finishing the current stage"],
  "services.waiting": ["等待存储", "Waiting for storage"],
  "services.blocked": ["处理需核对", "Processing needs review"],
  "services.stopped": ["服务未运行", "Service stopped"],
  "services.paused": ["已暂停", "Paused"],
  "services.unknown": ["状态待核对", "Check service status"],
  "services.continue.query": ["继续查询", "Resume queries"],
  "services.continue.intake": ["继续保存", "Resume saving"],
  "services.pause.query": ["暂停查询", "Pause queries"],
  "services.pause.intake": ["暂停保存", "Pause saving"],
  "services.update.query": ["更新查询服务", "Update query service"],
  "services.update.intake": ["更新保存服务", "Update saving service"],
  "services.history": [
    "历史未完成记录 {p0} 项，未自动恢复。",
    "{p0} unfinished historical records have not been resumed.",
  ],
  "services.diagnostic": ["服务诊断", "Service diagnostics"],
  "services.version": [
    "运行版本：{p0}；状态：{p1}",
    "Runtime version: {p0}; status: {p1}",
  ],
  "services.pause.transport": ["暂停接收", "Pause reception"],
  "services.error.0": [
    "服务状态已变化，请刷新后重试。",
    "The service state changed. Refresh and try again.",
  ],
  "services.error.1": [
    "当前阶段尚未结束，请等待服务停止后再启动。",
    "The current stage has not finished. Wait for the service to stop before starting it.",
  ],
  "services.error.2": [
    "该入口已启用独立运行，请刷新。",
    "Independent services are already enabled. Refresh this page.",
  ],
  "services.error.3": [
    "请先启用独立运行。",
    "Enable independent services first.",
  ],
  "services.error.4": [
    "此入口未配置该职责，请先检查入口配置。",
    "This role is not configured for this entry. Check the entry settings.",
  ],
  "services.error.5": [
    "机器人绑定不可用，请先核对绑定。",
    "The bot binding is unavailable. Check its binding first.",
  ],
  "services.error.6": [
    "服务启动失败，配置和历史记录保留，请查看诊断。",
    "The service failed to start. Settings and history are retained. Check diagnostics.",
  ],
  "services.error.7": [
    "服务运行版本校验失败，请重新准备该版本。",
    "Runtime verification failed. Prepare this version again.",
  ],
  "services.error.8": [
    "查询入口不能同时开启资料接收，请使用保存入口。",
    "Use a note saving entry to receive materials.",
  ],
  "services.error.9": [
    "笔记保存入口需要飞书机器人及资料接收权限。",
    "Note saving requires a Feishu bot and permission to receive materials.",
  ],
  "services.error.10": [
    "此入口负责笔记保存，请使用查询入口提问。",
    "This entry saves notes. Ask questions through a query entry.",
  ],
  "services.error.11": [
    "查询处理已暂停，请先继续查询服务。",
    "Query processing is paused. Resume the query service first.",
  ],
};
