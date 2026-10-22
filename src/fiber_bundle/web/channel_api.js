import { serviceErrorText } from "./channel_services_api.js";
import { channelIssuesText } from "./channel_validation.js";
// Channel-only HTTP contracts; no process or credential handling belongs in the renderer.
function overview(value) {
  return (
    value?.schema === "channel-overview/v1" &&
    Number.isFinite(value.observed_at) &&
    Array.isArray(value.issues) &&
    Array.isArray(value.channels) &&
    value.channels.length <= 100 &&
    value.channels.every(
      (item) =>
        item !== null &&
        typeof item === "object" &&
        typeof item.id === "string" &&
        typeof item.name === "string" &&
        ["gui", "legacy"].includes(item.origin) &&
        ["feishu", "weixin"].includes(item.platform) &&
        typeof item.binding_state === "string" &&
        typeof item.runtime?.process_state === "string" &&
        typeof item.runtime.heartbeat_fresh === "boolean" &&
        typeof item.transport === "string" &&
        typeof item.capabilities === "object" &&
        item.capabilities !== null &&
        ["query", "model", "capture", "download", "store"].every(
          (key) =>
            typeof item.capabilities[key]?.available === "boolean" &&
            typeof item.capabilities[key].enabled === "boolean",
        ) &&
        (item.receipts === undefined ||
          (typeof item.receipts?.enabled === "boolean" &&
            [
              "pending_batches",
              "needs_review_batches",
              "sending_batches",
            ].every(
              (key) =>
                Number.isSafeInteger(item.receipts[key]) &&
                item.receipts[key] >= 0,
            ))) &&
        Number.isFinite(item.observed_at) &&
        (item.origin === "legacy" ||
          (typeof item.configuration?.id === "string" &&
            typeof item.configuration.settings?.answer_mode === "string")) &&
        Number.isInteger(item.revision) &&
        typeof item.configuration_sha256 === "string",
    )
  );
}

export function createChannelApi(request) {
  const path = (id) => `/admin/channels/${encodeURIComponent(id)}`;
  const intake = (id) => `${path(id)}/intake`;
  const batch = (id, bid) => `${intake(id)}/batches/${encodeURIComponent(bid)}`;
  const link = (id, bid, index) => `${batch(id, bid)}/links/${index}`;
  return {
    channelReadiness: (id) =>
      request(
        `${path(id)}/start-readiness`,
        "GET",
        undefined,
        (value) =>
          typeof value?.ready === "boolean" &&
          Number.isInteger(value.revision) &&
          Array.isArray(value.issues) &&
          value.issues.every((code) => Boolean(channelIssuesText([code]))),
      ),
    channelExpertIntents: (id) => request(`${path(id)}/expert-intents`, "GET"),
    channelExpertReconcile: (id, event, body) =>
      request(
        `${path(id)}/expert-intents/${encodeURIComponent(event)}/reconcile`,
        "POST",
        body,
      ),
    channelIntake: (id) => request(intake(id), "GET"),
    channelIntakeSave: (id, body) => request(intake(id), "PUT", body),
    channelIntakeBatches: (id, body) =>
      request(`${intake(id)}/batches`, "POST", body),
    channelIntakeCancel: (id, body) =>
      request(`${intake(id)}/cancel`, "POST", body),
    channelIntakeOriginals: (id, bid) =>
      request(`${batch(id, bid)}/originals`, "GET"),
    channelIntakeOriginalURL: (id, bid, asset) =>
      `${batch(id, bid)}/originals/${encodeURIComponent(asset)}`,
    channelIntakePacket: (id, bid, index) =>
      request(link(id, bid, index), "GET"),
    channelIntakeAssetURL: (id, bid, index, name) =>
      `${link(id, bid, index)}/assets/${name.split("/").map(encodeURIComponent).join("/")}`,
    channelIntakeReview: (id, bid, index, body) =>
      request(`${link(id, bid, index)}/review`, "POST", body),
    channelIntakePublish: (id, bid, index, reviewId) =>
      request(`${link(id, bid, index)}/publish`, "POST", {
        review_id: reviewId,
      }),
    channelIntakeRetry: (id, bid, index, revision, approveNewAttempt = false) =>
      request(`${link(id, bid, index)}/retry`, "POST", {
        expected_revision: revision,
        ...(approveNewAttempt ? { approve_new_model_attempt: true } : {}),
      }),
    channelIntakeReceiptAck: (id, bid, state) =>
      request(`${batch(id, bid)}/receipt-ack`, "POST", {
        acknowledgement: state,
      }),
    channelOverview: () =>
      request("/admin/channels/overview", "GET", undefined, overview),
    channelLifecyclePreview: (id, action) =>
      request(`${path(id)}/lifecycle/preview`, "POST", { action }),
    channelLifecycle: (id, body) =>
      request(`${path(id)}/lifecycle`, "POST", body),
    channelLifecycleResume: (id, operationId) =>
      request(
        `${path(id)}/lifecycle/${encodeURIComponent(operationId)}/resume`,
        "POST",
      ),
    legacyChannelStart: (id, body) =>
      request(`${path(id)}/legacy-start`, "POST", body),
  };
}

export function channelErrorText(code, issues = []) {
  if (serviceErrorText(code)) return serviceErrorText(code);
  if (["channel_configuration_invalid", "channel_input_invalid"].includes(code))
    return (
      channelIssuesText(issues) ||
      "连接配置校验未通过，请刷新入口配置后核对字段提示。"
    );
  return {
    channel_runtime_manifest_unreadable:
      "无法安全读取Hermes运行清单，请核对‘Hermes固定运行清单绝对路径’、文件是否存在及本机读取权限，再验证组件。",
    channel_runtime_manifest_invalid:
      "Hermes固定运行清单格式或组件校验失败，请检查‘Hermes固定运行清单绝对路径’并重新校验/安装对应组件。",
    channel_runtime_manifest_local_required:
      "‘Hermes固定运行清单绝对路径’必须位于当前应用数据目录内，请选择本实例安装的清单。",
    channel_credential_invalid:
      "App Secret格式不符合当前凭据校验要求，请核对‘App Secret’或本机凭证文件；不要把App ID填入Secret。",
    channel_credential_required:
      "此入口尚无可用凭据，或App ID已改变；请填写‘App Secret’或选择本机凭证文件后保存。",
    channel_feishu_app_required:
      "飞书入口缺少‘App ID’，请填写飞书自建应用的App ID。",
    channel_model_policy_unavailable:
      "所选回答方式没有可用模型路由，请先在‘模型与查询’配置，或改用‘原文证据，零模型调用’。",
    channel_expert_requires_feishu_add_original_limit:
      "已启用专家文件指令，请同时选择飞书、勾选‘允许取回原件’和‘允许入库’，并将‘本次配置的专家指令上限’设为正数。",
    channel_expert_disabled_limits: "专家指令已关闭，请将两项专家额度设为0。",
    channel_pairing_candidate_changed:
      "配对候选已过期或配置已变化，请重新‘开始本人配对’并确认本次候选。",
    channel_pause_or_revision_required:
      "入口仍在运行或配置版本已变化，请先暂停并刷新，再保存配置。",
    channel_launch_failed:
      "本机未能启动入口进程，请检查后台运行环境；不要修改权限或额度来重试此错误。",
    intake_article_extraction_disabled:
      "文章正文提取未开启：请在‘自动入库与网站白名单’勾选‘归档链接后，提取可取得的 X 长文章 / 公众号正文及原图’，保存策略后再明确重试。",
    intake_subtitle_extraction_disabled:
      "视频提取未开启：请在‘自动入库与网站白名单’勾选元数据/已有字幕获取，保存策略后再明确重试。",
    intake_extractor_unavailable:
      "当前网站没有可用正文/字幕提取器，仅保留链接；请提供正文文件或使用支持的网站来源。",
    intake_revision_conflict:
      "自动入库策略已变化，请核对当前版本；草稿已保留。",
    intake_target_required: "请先选择一个已有可写授权的目标知识库。",
    intake_store_limit_required:
      "开启自动入库需要勾选资料类型并设置大于零的累计归档上限。",
    intake_owner_or_channel_unavailable: "请先完成本人配对，并检查入口状态。",
    intake_stop_before_recovery: "请先暂停入口并等待退出，再核对中断操作。",
    intake_download_requires_reconciliation:
      "有资源下载中断，需先核对原下载状态。",
    intake_site_not_allowed: "此链接没有列入网站白名单，请核对勾选。",
    intake_target_changed: "此批次的目标库与当前策略不同，已停止处理。",
    intake_add_permission_required: "请先给此入口启用资料入库权限。",
    intake_read_only_vault: "目标知识库为只读，不能自动入库。",
    intake_policy_changed: "入口、授权或策略已变化，请刷新后核对。",
    channel_revision_conflict:
      "连接配置或实例已变化，请刷新后重新确认；当前编辑已保留。",
    channel_operation_pending: "此连接有未完成的停止操作，请先核对操作进度。",
    channel_process_not_stopped:
      "进程尚未确认退出，凭证保留；请刷新后继续核对。",
    channel_legacy_process_unverified:
      "无法确认既有进程身份，已保留连接；请先手动停止该项目 gateway。",
    channel_removed: "此连接已移除，请刷新列表。",
    channel_restored_confirmation_required:
      "恢复已中断旧操作，请重新预览并确认本次操作。",
    channel_pause_required: "请先暂停并等待 Hermes 退出，再操作此连接。",
    channel_instance_already_running:
      "此入口的 Hermes 实例仍在运行，请先暂停。",
    channel_explicit_budgets_required:
      "请先配置查询及手机回复额度；使用模型时还需模型额度。",
    channel_delivery_requires_reconciliation:
      "有发送结果待核对，请查看消息执行与发送记录。",
    channel_pairing_required: "请先完成本人配对或扫码，并在本机确认。",
    channel_cleanup_requires_review:
      "本地文件归属无法确认，凭证清理已停止；请查看操作详情。",
    channel_legacy_start_unavailable:
      "既有入口缺少固定运行清单，请通过新增入口重新配置。",
  }[code];
}
