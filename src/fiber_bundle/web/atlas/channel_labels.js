// Bilingual wording for channel runtime states and intake reasons. Unknown codes fall back
// to "needs checking" so raw codes never masquerade as healthy states.
import { lang } from "./i18n.js";

const zh = {
  connected: "已连接",
  disconnected: "已断开",
  connecting: "连接中",
  unknown: "未确认",
  running: "运行中",
  stopped: "已停止",
  stopping: "停止中",
  unpaired: "未绑定",
  bound: "已绑定",
  awaiting_pair: "等待配对消息",
  awaiting_scan: "等待扫码",
  awaiting_binding: "等待绑定 / 登录",
  awaiting_confirmation: "等待本机确认",
  candidate_expired: "配对确认已过期",
  configuration_required: "需要重新配置",
  login_required: "需要重新登录",
  paused: "已暂停",
  error: "异常，需核对",
  pair: "等待配对",
  login: "等待扫码",
  check: "验证中",
  removing: "移除中",
  unbinding: "解绑中",
  removed: "已移除",
  not_started: "尚未启动",
  process_exited: "进程已退出",
  heartbeat_missing: "尚无心跳",
  heartbeat_expired: "心跳已过期",
  instance_mismatch: "实例身份不匹配",
  status_invalid: "状态文件无效或无法读取",
  channel_status_missing: "通道运行状态尚未确认",
  channel_revision_conflict: "配置或实例已变化，请核对后继续",
  transport_or_processing_unavailable: "平台连接或处理状态尚未就绪",
  binding_required: "请先完成本人绑定",
  permission_revoked: "查询授权不可用",
  policy_changed: "模型配置已变化",
  channel_paused: "入口已暂停",
  budget_or_switch_disabled: "开关关闭或额度不足",
  runtime_unavailable: "连接尚未就绪",
  delivery_requires_reconciliation: "发送结果待核对",
  channel_delivery_requires_reconciliation:
    "发送结果待核对；请在“消息记录”中于手机确认后继续",
  channel_credentials_rejected: "应用凭证验证失败，请检查 App ID 与 Secret",
  channel_connection_failed: "平台连接失败，请检查网络与应用配置",
  channel_connection_stopped: "平台连接已中止，请核对后重新连接",
  channel_weixin_login_not_completed: "微信登录未完成，请重新扫码",
  channel_weixin_poll_rejected: "微信连接被拒绝，请重新登录",
  channel_operation_failed: "验证或连接失败，请核对配置后重试",
  permission_or_runtime_unavailable: "权限或连接尚未就绪",
  download_budget_or_runtime_unavailable: "下载额度或连接尚未就绪",
  manual_confirmation_or_budget_required: "需人工确认或配置处理额度",
  needs_review: "待核对",
  completed: "已完成",
  interrupted: "恢复后已中断，须重新确认",
  pending: "正在处理",
  channel_process_not_stopped: "Hermes 尚未确认退出，凭证保留",
  channel_legacy_process_unverified:
    "既有进程身份未确认，请先手动停止该项目 gateway",
  channel_cleanup_requires_review: "文件归属需核对，清理已停止",
  channel_operation_interrupted: "操作已中断，可继续核对",
  fallback: "待核对",
};
const en = {
  connected: "Connected",
  disconnected: "Disconnected",
  connecting: "Connecting",
  unknown: "Unconfirmed",
  running: "Running",
  stopped: "Stopped",
  stopping: "Stopping",
  unpaired: "Not bound",
  bound: "Bound",
  awaiting_pair: "Waiting for pairing message",
  awaiting_scan: "Waiting for scan",
  awaiting_binding: "Waiting for binding / sign-in",
  awaiting_confirmation: "Waiting for local confirmation",
  candidate_expired: "Pairing confirmation expired",
  configuration_required: "Needs reconfiguring",
  login_required: "Needs sign-in again",
  paused: "Paused",
  error: "Error — check",
  pair: "Waiting to pair",
  login: "Waiting for scan",
  check: "Checking",
  removing: "Removing",
  unbinding: "Unbinding",
  removed: "Removed",
  not_started: "Not started",
  process_exited: "Process exited",
  heartbeat_missing: "No heartbeat yet",
  heartbeat_expired: "Heartbeat expired",
  instance_mismatch: "Instance identity mismatch",
  status_invalid: "Status file invalid or unreadable",
  channel_status_missing: "Runtime state not confirmed",
  channel_revision_conflict:
    "Configuration or instance changed — check before continuing",
  transport_or_processing_unavailable:
    "Platform connection or processing not ready",
  binding_required: "Complete owner binding first",
  permission_revoked: "Query access unavailable",
  policy_changed: "Model configuration changed",
  channel_paused: "Channel paused",
  budget_or_switch_disabled: "Switch off or quota exhausted",
  runtime_unavailable: "Connection not ready",
  delivery_requires_reconciliation: "Delivery result needs reconciling",
  channel_delivery_requires_reconciliation:
    "Delivery result needs reconciling; confirm on the phone via Message log, then continue",
  channel_credentials_rejected:
    "App credentials rejected — check App ID and Secret",
  channel_connection_failed:
    "Platform connection failed — check network and app settings",
  channel_connection_stopped:
    "Platform connection stopped — check, then reconnect",
  channel_weixin_login_not_completed:
    "WeChat sign-in not completed — scan again",
  channel_weixin_poll_rejected: "WeChat connection rejected — sign in again",
  channel_operation_failed:
    "Check or connection failed — review the configuration and retry",
  permission_or_runtime_unavailable: "Permission or connection not ready",
  download_budget_or_runtime_unavailable:
    "Download quota or connection not ready",
  manual_confirmation_or_budget_required:
    "Needs manual confirmation or a processing quota",
  needs_review: "Needs checking",
  completed: "Completed",
  interrupted: "Interrupted after recovery — confirm again",
  pending: "In progress",
  channel_process_not_stopped: "Hermes hasn't confirmed exit; credentials kept",
  channel_legacy_process_unverified:
    "Existing process identity unconfirmed — stop that project's gateway manually first",
  channel_cleanup_requires_review:
    "File ownership needs checking; cleanup stopped",
  channel_operation_interrupted:
    "Operation interrupted — you can continue checking",
  fallback: "Needs checking",
};
const intakeZh = {
  waiting: "附件待补齐",
  queued: "等待归档",
  archiving: "归档中 / 中断待核对",
  stored: "原件已归档",
  manual: "待手动确认",
  needs_review: "待核对 / 可明确重试",
  link_only: "仅链接已保存",
  extracting: "提取中 / 中断待核对",
  review_required: "正文 / 字幕待审核",
  metadata_only: "仅来源信息；正文待补全",
  published: "已审核发布",
  sent: "回执已发送",
  unknown: "回执结果待手机核对",
  sending: "回执发送中 / 中断待核对",
  budget_exhausted: "回执额度不足",
  not_sent: "已核对未发送",
  off: "提取未开启",
  pending: "等待提取",
  complete: "提取尝试已结束",
  cancelled: "提取已取消",
  intake_site_not_allowed: "包含未允许的网站，整批保留待确认。",
  intake_material_not_selected: "包含未勾选的资料类型。",
  intake_command_requires_routing: "查询或操作命令不会作为资料保存。",
  intake_message_unsupported: "存在未支持的消息部分，已保留原事件。",
  intake_attachments_missing: "附件待下载；请核对获取额度。",
  intake_download_budget_exhausted: "获取额度不足，请调整累计上限后明确重试。",
  intake_store_budget_exhausted: "归档额度不足，请调整累计上限后处理所选批次。",
  intake_policy_changed: "策略已改变，请明确选择是否处理此批次。",
  intake_contains_historical_messages: "包含策略生效前的消息，需要手动确认。",
  intake_user_cancelled: "已停止自动处理，原消息保留。",
  intake_archive_interrupted: "保存中断，需明确恢复；不会自动重复写入。",
  intake_batch_changed: "批次已被选择或取消，请核对。",
  intake_content_incomplete: "正文或图片不完整，原链接保留，未发布。",
  intake_extractor_unavailable: "没有启用的提取器，仅链接已保存。",
  intake_fetch_failed: "获取失败，请核对来源后明确重试。",
  intake_video_access_restricted: "视频需要登录或不可访问。",
  intake_subtitles_unavailable: "未取得已有字幕，正文待补全。",
  intake_subtitles_access_restricted: "字幕需要登录，正文待补全。",
  intake_video_duration_over_limit: "视频超过当前 60 秒范围，仅保存链接。",
  intake_video_duration_unverified: "视频时长无法核实，仅保存链接。",
  intake_channels_content_unavailable:
    "视频号内容目前无法直接取得，已保留来源待补全。",
  intake_redirect_not_allowed: "跳转目标不在白名单，已停止获取。",
  intake_read_only_vault: "目标知识库只读，不能自动写入。",
  intake_target_required: "请选择已有可写授权的目标库。",
  intake_revision_conflict: "策略已在其他窗口改变；请刷新核对，当前草稿保留。",
  intake_permission_unavailable: "入库授权不可用，请检查客户端权限。",
  intake_add_permission_required: "此入口尚未启用资料入库权限。",
  intake_target_unavailable: "知识库目录或范围不可用，请核对接入状态。",
  intake_owner_or_channel_unavailable: "入口尚未绑定本人或已解除。",
  intake_channel_paused: "入口未运行，新任务暂不执行。",
  intake_binding_changed: "本人绑定已改变，请核对并重新保存策略。",
  intake_download_requires_reconciliation:
    "附件下载中 / 中断待核对；不会自动重试。",
  intake_attachment_download_failed: "附件获取失败，可明确选择此批次重试。",
  fallback: "需要核对，详情见诊断。",
};
const intakeEn = {
  waiting: "Attachments pending",
  queued: "Waiting to archive",
  archiving: "Archiving / interrupted — check",
  stored: "Originals archived",
  manual: "Needs manual confirmation",
  needs_review: "Needs checking / explicit retry",
  link_only: "Link saved only",
  extracting: "Extracting / interrupted — check",
  review_required: "Text / subtitles need review",
  metadata_only: "Source info only; text to complete",
  published: "Reviewed and published",
  sent: "Receipt sent",
  unknown: "Receipt result to check on phone",
  sending: "Receipt sending / interrupted — check",
  budget_exhausted: "Receipt quota exhausted",
  not_sent: "Confirmed not sent",
  off: "Extraction off",
  pending: "Waiting to extract",
  complete: "Extraction attempt finished",
  cancelled: "Extraction cancelled",
  intake_site_not_allowed:
    "Contains a site that isn't allowed; the whole batch waits for confirmation.",
  intake_material_not_selected: "Contains a material type that isn't selected.",
  intake_command_requires_routing:
    "Query or command messages are never saved as material.",
  intake_message_unsupported:
    "Unsupported message parts; the original event is kept.",
  intake_attachments_missing:
    "Attachments still to download; check the fetch quota.",
  intake_download_budget_exhausted:
    "Fetch quota exhausted; raise the limit, then retry explicitly.",
  intake_store_budget_exhausted:
    "Archive quota exhausted; raise the limit, then process the chosen batches.",
  intake_policy_changed:
    "Policy changed; choose explicitly whether to process this batch.",
  intake_contains_historical_messages:
    "Contains messages from before the policy; needs manual confirmation.",
  intake_user_cancelled:
    "Automatic processing stopped; original messages kept.",
  intake_archive_interrupted:
    "Saving was interrupted; recover explicitly (no automatic re-write).",
  intake_batch_changed: "The batch was chosen or cancelled — check it.",
  intake_content_incomplete:
    "Text or images incomplete; the link is kept and nothing was published.",
  intake_extractor_unavailable: "No extractor enabled; link saved only.",
  intake_fetch_failed: "Fetch failed; check the source, then retry explicitly.",
  intake_video_access_restricted: "The video needs sign-in or is inaccessible.",
  intake_subtitles_unavailable: "No existing subtitles; text to complete.",
  intake_subtitles_access_restricted:
    "Subtitles need sign-in; text to complete.",
  intake_video_duration_over_limit:
    "Video is longer than the 60 s limit; link saved only.",
  intake_video_duration_unverified:
    "Video duration can't be verified; link saved only.",
  intake_channels_content_unavailable:
    "Channels content can't be fetched directly; source kept for completion.",
  intake_redirect_not_allowed:
    "Redirect target isn't allow-listed; fetch stopped.",
  intake_read_only_vault: "The target vault is read-only.",
  intake_target_required: "Choose a target vault with write access.",
  intake_revision_conflict:
    "The policy changed in another window; refresh to check (your draft is kept).",
  intake_permission_unavailable:
    "Filing permission unavailable; check client grants.",
  intake_add_permission_required:
    "This channel doesn't have filing permission yet.",
  intake_target_unavailable:
    "Vault folder or scope unavailable; check its connection.",
  intake_owner_or_channel_unavailable: "Channel not bound to you, or unbound.",
  intake_channel_paused: "Channel not running; new tasks wait.",
  intake_binding_changed:
    "Owner binding changed; check and save the policy again.",
  intake_download_requires_reconciliation:
    "Attachment download in progress / interrupted — check (no automatic retry).",
  intake_attachment_download_failed:
    "Attachment fetch failed; you can choose this batch to retry.",
  fallback: "Needs checking — see diagnostics.",
};
export function channelLabel(code) {
  const table = lang() === "en" ? en : zh;
  return table[code] || table.fallback;
}
export function intakeLabel(code) {
  if (!code) return "";
  const table = lang() === "en" ? intakeEn : intakeZh;
  return table[code] || table.fallback;
}
export const channelTone = (code) =>
  ["connected", "running", "bound", "completed"].includes(code)
    ? "success"
    : [
          "error",
          "disconnected",
          "process_exited",
          "heartbeat_expired",
          "instance_mismatch",
          "status_invalid",
        ].includes(code)
      ? "error"
      : ["stopped", "paused", "removed", "not_started"].includes(code)
        ? "paused"
        : "waiting";
