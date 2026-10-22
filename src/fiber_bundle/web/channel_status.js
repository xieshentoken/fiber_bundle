import { joinText, phrase, setText, uiText } from "./i18n/index.js";
import { el } from "./ui.js";
import { intakeLabel } from "./channel_intake_records.js";

const labels = {
  connected: "已连接",
  disconnected: "已断开",
  connecting: "连接中",
  reconnecting: "网络中断，正在重连",
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
  model_unconfigured: "尚未配置默认查询模型",
  model_disabled: "默认查询模型或凭据已停用",
  channel_paused: "入口已暂停",
  budget_or_switch_disabled: "开关关闭或额度不足",
  runtime_unavailable: "连接尚未就绪",
  delivery_requires_reconciliation: "发送结果待核对",
  channel_delivery_requires_reconciliation:
    "发送结果待核对；请打开消息执行与发送记录，在手机确认后继续操作",
  channel_credentials_rejected: "应用凭证验证失败，请检查 App ID 与 Secret",
  channel_connection_failed: "平台连接失败，请检查网络与应用配置",
  channel_connection_stopped: "平台连接已中止，请核对后重新连接",
  channel_weixin_login_not_completed: "微信登录未完成，请重新扫码",
  channel_weixin_poll_rejected:
    "微信接收接口拒绝请求，请核对连接诊断及登录状态",
  channel_weixin_poll_exhausted:
    "微信接收网络连续失败，已停止；检查网络后重新连接，无需重复发送问题",
  channel_weixin_response_invalid:
    "微信接收响应格式异常，已停止；请查看连接诊断",
  channel_operation_failed:
    "旧记录未保留具体原因，请查看连接诊断；不要据此修改凭据或权限",
  channel_capture_failed:
    "消息接收后的本机保存失败，入口已停止。请查看连接诊断并核对后台存储与入库授权",
  channel_storage_failed:
    "本机数据库操作失败，入口已停止。请核对磁盘空间及后台状态，查看连接诊断",
  channel_local_io_failed:
    "本机文件或网络I/O失败，入口已停止。请检查运行清单、目录访问及网络，查看连接诊断",
  channel_connection_timeout: "平台连接或断开操作超时，请核对网络后重新连接",
  channel_runtime_validation_failed:
    "本机运行时校验未通过，请校验Hermes组件与运行清单，查看连接诊断",
  channel_internal_failed:
    "本机入口处理发生内部错误，请提供连接诊断中的模块与行号排查；无需盲目调整额度",
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
};
export const channelLabel = (value) => labels[value] || "待核对";

export function intakeStatusText(entry, stale = false) {
  if (!entry.intake) return "";
  const intake = entry.intake;
  if (stale) return "自动入库状态已过期，请刷新。";
  if (!intake.enabled) return "自动入库已关闭。";
  return [
    intake.reason ? intakeLabel(intake.reason) : "自动入库已就绪。",
    intake.worker?.state === "processing" ? "正在处理资料。" : "",
    intake.pending_review ? `${intake.pending_review} 批待恢复或核对。` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

export function channelStatusText(entry, stale = false) {
  return {
    binding: channelLabel(entry.binding_state),
    process: stale ? "状态已过期" : channelLabel(entry.runtime.process_state),
    transport: stale ? "状态已过期，待刷新" : channelLabel(entry.transport),
    query: stale
      ? "待刷新"
      : entry.capabilities.query.available
        ? "可用"
        : "暂不可用",
  };
}

export function receiptStatusText(entry, stale = false) {
  const receipts = entry.receipts;
  if (!receipts) return "";
  if (stale) return phrase("回执状态已过期，请刷新。");
  const parts = [phrase(receipts.enabled ? "回执已开启。" : "新回执已关闭。")];
  const held = receipts.needs_review_batches + receipts.sending_batches;
  if (receipts.needs_review_batches)
    parts.push(
      phrase("{p0} 批回执待核对，不会自动重发。", {
        p0: receipts.needs_review_batches,
      }),
    );
  if (receipts.sending_batches)
    parts.push(
      phrase("{p0} 批回执发送待确认，不会重复发送。", {
        p0: receipts.sending_batches,
      }),
    );
  if (receipts.enabled) {
    if (receipts.pending_batches)
      parts.push(
        phrase("{p0} 批回执待发送或更新。", { p0: receipts.pending_batches }),
      );
    if (held) parts.push(phrase("新批次回执独立发送。"));
    if (entry.transport !== "connected")
      parts.push(phrase("平台连接恢复后继续发送。"));
  }
  return joinText(parts, " ");
}

export function createChannelStatus() {
  const root = el("div", null, "channel-status"),
    grid = el("dl", null, "channel-status-grid"),
    nodes = {};
  for (const [key, title] of [
    ["binding", "账号绑定"],
    ["process", "后台进程"],
    ["transport", "平台通道"],
    ["query", "查询服务"],
  ]) {
    const row = el("div");
    nodes[key] = el("dd");
    nodes[key].dataset.status = key;
    row.append(el("dt", uiText(title)), nodes[key]);
    grid.append(row);
  }
  const reason = el("p", null, "muted"),
    capability = el("p", null, "muted"),
    intake = el("p", null, "muted"),
    receipt = el("p", null, "muted"),
    updated = el("p", null, "muted"),
    operation = el("p", null, "channel-operation");
  receipt.dataset.receiptStatus = "true";
  root.append(grid, reason, capability, intake, receipt, updated, operation);
  return {
    root,
    update(entry, stale = false) {
      const text = channelStatusText(entry, stale);
      for (const [key, value] of Object.entries(text))
        setText(nodes[key], uiText(value));
      root.dataset.stale = String(stale);
      setText(
        reason,
        stale
          ? uiText("上次观测已过期，请刷新连接状态。")
          : entry.state === "error" &&
              entry.configuration?.connection_check?.error
            ? uiText(channelLabel(entry.configuration.connection_check.error))
            : entry.runtime.reason
              ? uiText(channelLabel(entry.runtime.reason))
              : entry.capabilities.query.reason
                ? uiText(channelLabel(entry.capabilities.query.reason))
                : uiText(""),
      );
      setText(
        capability,
        joinText(
          [
            ["capture", "接收待审资料"],
            ["download", "附件下载"],
            ["store", "自动入库"],
            ["model", "模型回复"],
          ].map(([key, name]) =>
            joinText([
              uiText(name),
              ": ",
              uiText(
                stale
                  ? "待刷新"
                  : entry.capabilities[key].available
                    ? "可用"
                    : "未就绪",
              ),
            ]),
          ),
          " · ",
        ),
      );
      const processing = intakeStatusText(entry, stale);
      setText(
        intake,
        processing ? phrase("资料处理：{p0}", { p0: uiText(processing) }) : "",
      );
      setText(receipt, receiptStatusText(entry, stale));
      receipt.hidden = !receipt.textContent;
      intake.hidden = !intake.textContent;
      intake.dataset.intakeStatus = entry.intake?.worker?.state || "unobserved";
      setText(
        updated,
        phrase("最近观测 {p0}", {
          p0: new Date(entry.observed_at * 1000).toLocaleTimeString(),
        }),
      );
      setText(
        operation,
        entry.operation
          ? joinText([
              "",
              { remove: "移除连接", unbind: "解除绑定", pause: "暂停连接" }[
                entry.operation.action
              ] || "连接操作",
              " · ",
              uiText(channelLabel(entry.operation.state)),
              "",
              entry.operation.reason
                ? " · " + channelLabel(entry.operation.reason)
                : "",
              "",
            ])
          : uiText(""),
      );
    },
  };
}

export function renderChannelOverview(root, overview, stale = false) {
  const nodes = [
    el(
      "p",
      stale
        ? uiText("连接状态已过期，请刷新。")
        : phrase("最近观测 {p0}；页面自动观察最多三分钟。", {
            p0: new Date(overview.observed_at * 1000).toLocaleTimeString(),
          }),
    ),
  ];
  for (const [platform, name] of [
    ["feishu", "飞书"],
    ["weixin", "微信"],
  ]) {
    const entries = overview.channels.filter(
      (item) => item.platform === platform,
    );
    nodes.push(
      el(
        "p",
        joinText([
          "",
          uiText(name),
          ": ",
          stale
            ? uiText("待刷新")
            : entries.length
              ? phrase("{p0} / {p1} 个入口已连接", {
                  p0: entries.filter((item) => item.transport === "connected")
                    .length,
                  p1: entries.length,
                })
              : uiText("尚未配置"),
          "",
        ]),
      ),
    );
  }
  if (overview.issues.length)
    nodes.push(
      el(
        "p",
        uiText("部分既有入口配置无法读取，请核对项目 gateway 配置。"),
        "error",
      ),
    );
  root.replaceChildren(...nodes);
}
