import {
  createQueryScopeEditor,
  relationCard,
  relationOverview,
} from "../channel_query_scope.js";
import { createChannelServices, channelPurpose } from "../channel_services.js";
import { createChannelStatus } from "../channel_status.js";
import { featureControls } from "./feature_controls.js";
import { lang } from "./i18n.js";
// Message channels (Feishu / WeChat). Saving a configuration never starts a model or sends a
// message; pairing, starting, pausing, unbinding and removing are explicit actions.
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  checkbox,
  codeBlock,
  disclosure,
  empty,
  failure,
  field,
  input,
  notice,
  select,
  skeleton,
  stat,
  tabs,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm, sheet } from "./dialog.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";
import { channelLabel, channelTone, intakeLabel } from "./channel_labels.js";
import { createIntake } from "./channel_intake.js";
import { timeOnly } from "./format.js";

const t = strings({
  zh: {
    new: "新增飞书 / 微信入口",
    feishu: "飞书",
    weixin: "微信",
    connectedOf: "{c} / {n} 已连接",
    notSet: "尚未配置",
    stale: "状态已过期，待刷新",
    observed: "最近观测 {time} · 页面自动观察最多三分钟",
    issues: "部分既有入口配置无法读取，请核对项目 gateway 配置。",
    none: "尚无消息入口。新增一个飞书或微信连接开始。",
    legacy: "本项目既有 gateway",
    gui: "GUI 创建的独立入口",
    revision: "配置版本 {n}",
    binding: "账号绑定",
    process: "Hermes 进程",
    transport: "平台连接",
    query: "查询与回复",
    available: "可用",
    unavailable: "暂不可用",
    refreshLater: "待刷新",
    capture: "接收待审资料",
    download: "附件下载",
    store: "自动入库",
    model: "模型回复",
    e2e: "手机收发：尚未进行本轮端到端验证",
    op_remove: "移除连接",
    op_unbind: "解除绑定",
    op_pause: "暂停连接",
    op_other: "连接操作",
    edit: "编辑配置",
    check: "验证连接",
    pairFeishu: "开始本人配对",
    pairWeixin: "扫码登录 / 重新登录",
    confirmOwner: "确认本人：{c}",
    start: "启动 / 重新连接",
    pause: "暂停接收",
    unbind: "解除绑定",
    remove: "移除连接",
    resume: "继续核对操作",
    removeTitle: "移除这个消息入口？",
    unbindTitle: "解除本人绑定？",
    keep: "保留入口",
    lifecycleDone: "操作已记录。请查看停止与清理进度，笔记和执行审计会保留。",
    challenge: "请用本人账号私聊机器人发送以下完整文字，再在本机确认：",
    qr: "显示 / 刷新真实登录二维码",
    qrAlt: "微信本人登录二维码",
    details: "详情与记录",
    intakeOn: "自动入库已开启 · 目标库：{v}",
    intakeOff: "自动入库已关闭",
    intakeUnset: "自动入库策略尚未配置，默认关闭。",
    tab_intake: "自动入库",
    tab_expert: "fake-expert 指令",
    tab_test: "查询测试",
    tab_events: "消息记录",
    tab_diag: "诊断",
    expertOnlyFeishu: "此入口不支持飞书专家指令。",
    expertNone: "尚无明确专家文件指令。",
    ex_waiting_file: "等待本私聊上传 PDF",
    ex_needs_selection: "等待选择文件",
    ex_awaiting_download: "等待文件下载",
    ex_bound: "来源已绑定",
    ex_queued: "专家任务待执行",
    ex_submitted: "专家任务已提交",
    ex_cancelled: "意图已取消",
    ex_expired: "等待已到期",
    ex_failed: "需核对",
    expertTask: "专家任务：{id}；在资料导入中查看提取 / 补充 workpack 状态。",
    expertExtra: "普通处理已经开始或完成，原结果保留；本次为额外专家处理。",
    ack: "手机回执：{s}",
    ack_sent: "已发送",
    ack_not_sent: "人工核实未发送",
    ack_sending: "发送结果待核对",
    ack_unknown: "发送结果未知",
    ackCheck: "先暂停入口，在手机核实后登记；不会重发或恢复额度。",
    markSent: "登记已发送",
    markNotSent: "登记未发送",
    testQuestion: "测试问题",
    testPlaceholder: "输入问题，或查询[库别名]：问题",
    testPreview: "测试原文查询（零模型调用）",
    testModel: "明确执行所选模型查询",
    testModelTitle: "执行一次模型查询？",
    testModelBody:
      "会使用此入口的模型额度；结果和费用可在“提问与检索”与“执行与费用”中核对。",
    previewDone: "原文查询完成，没有模型或手机发送。",
    modelDone: "模型任务已显式启动，可在提问与检索中核对结果和费用。",
    loadEvents: "刷新执行记录",
    noEvents: "暂无执行记录。",
    eventCheck: "请先暂停入口，并在手机核对是否收到。核对不会重发。",
    phoneGot: "手机已收到",
    phoneNot: "手机确认未收到",
    reconciled: "核对已记录，不会自动重发。",
    archived: "已移除连接与审计记录（{n}）",
    archivedRow: "{name} · 已移除；笔记、额度与执行审计保留。",
    archivedEvents: "查看保留的执行记录",
    legacyAudit: "既有入口的消息与批次审计保留在原账本中。",
    steps: "平台准备步骤",
    stepFeishu:
      "飞书：在开放平台创建独立自建应用，启用机器人，开通接收消息事件与发送消息权限，选择长连接并发布。不要复用其他 Hermes Bot 的应用。保存后验证连接，再开始本人配对并在本机确认。",
    stepWeixin:
      "微信：保存独立入口后点击扫码登录，二维码来自 Hermes 实际登录接口；本人扫码后在本机确认。历史消息无法直接转给 bot，可经“其他应用”转到飞书。",
    stepPerms: "只读查询只需查询权限；入库和原件权限独立。",
    configTitle: "入口配置",
    basics: "基本信息与凭证",
    name: "名称",
    platform: "平台",
    appId: "App ID",
    secret: "App Secret（保存后清空）",
    credential: "或导入本机凭证文件绝对路径",
    manifest: "Hermes 固定运行清单绝对路径",
    access: "身份与权限",
    client: "本人访问身份",
    vault: "默认知识库",
    accessHelp:
      "保存会为所选身份设置本库查询权限，并按下面选择设置原件和入库权限。",
    original: "允许取回原件",
    add: "允许入库（只读库不可选）",
    answer: "回答方式",
    evidence: "原文证据，零模型调用",
    local: "本地引用草稿",
    local_cloud: "本地引用草稿 + 云端整理",
    policyOn: "使用“模型与预算”的配置 {sha}；本地检索后可显式云端整理。",
    policyOff: "尚未配置回答模型，可先使用原文查询。",
    cloudHint: "云端仅整理本地答案及其中引文，不直接读取整库；失败不会切备用。",
    limits: "本配置版本的有限额度",
    limitsHelp: "暂停 / 重新启动不重置；新版本需重新确认额度。每项 0–100 次。",
    query_limit: "最多接收查询",
    model_limit: "最多模型调用",
    outbound_limit: "最多手机回复",
    download_limit: "最多飞书附件下载",
    expert: "私聊 fake-expert 文件指令",
    expert_enabled: "启用私聊 fake-expert 文件指令（须新增与原件权限）",
    expert_limit: "本次配置的专家指令上限",
    expert_receipt_limit: "专家状态回执上限（独立出站许可）",
    save: "保存配置与所选权限",
    saved: "配置与所选权限已保存；请完成本人绑定，再启动入口。",
    discardConfig: "入口配置尚未保存，关闭会丢失这些更改。确定关闭？",
  },
  en: {
    new: "New Feishu / WeChat channel",
    feishu: "Feishu",
    weixin: "WeChat",
    connectedOf: "{c} / {n} connected",
    notSet: "Not configured",
    stale: "State stale — refresh",
    observed: "Observed {time} · the page watches for up to three minutes",
    issues:
      "Some existing channel configurations can't be read; check the project gateway configuration.",
    none: "No message channels yet. Add a Feishu or WeChat connection to start.",
    legacy: "Existing project gateway",
    gui: "Standalone channel created in the GUI",
    revision: "Config revision {n}",
    binding: "Account binding",
    process: "Hermes process",
    transport: "Platform connection",
    query: "Query & reply",
    available: "Available",
    unavailable: "Unavailable",
    refreshLater: "Refresh",
    capture: "Capture for review",
    download: "Attachment download",
    store: "Auto-filing",
    model: "Model replies",
    e2e: "Phone send/receive: not end-to-end verified this round",
    op_remove: "Removing",
    op_unbind: "Unbinding",
    op_pause: "Pausing",
    op_other: "Operation",
    edit: "Edit configuration",
    check: "Check connection",
    pairFeishu: "Start owner pairing",
    pairWeixin: "Scan to sign in / sign in again",
    confirmOwner: "Confirm owner: {c}",
    start: "Start / reconnect",
    pause: "Pause channel",
    unbind: "Unbind",
    remove: "Remove channel",
    resume: "Continue checking operation",
    removeTitle: "Remove this message channel?",
    unbindTitle: "Unbind the owner?",
    keep: "Keep channel",
    lifecycleDone:
      "Operation recorded. Watch stop and cleanup progress; notes and audit are kept.",
    challenge:
      "From your own account, send this exact text to the bot in a private chat, then confirm here:",
    qr: "Show / refresh the real sign-in QR code",
    qrAlt: "WeChat owner sign-in QR code",
    details: "Details & logs",
    intakeOn: "Auto-filing on · target: {v}",
    intakeOff: "Auto-filing off",
    intakeUnset: "Auto-filing not configured (off by default).",
    tab_intake: "Auto-filing",
    tab_expert: "fake-expert commands",
    tab_test: "Query test",
    tab_events: "Message log",
    tab_diag: "Diagnostics",
    expertOnlyFeishu: "This channel doesn't support Feishu expert commands.",
    expertNone: "No explicit expert file commands yet.",
    ex_waiting_file: "Waiting for a PDF in this private chat",
    ex_needs_selection: "Waiting for file choice",
    ex_awaiting_download: "Waiting for download",
    ex_bound: "Source bound",
    ex_queued: "Expert task queued",
    ex_submitted: "Expert task submitted",
    ex_cancelled: "Intent cancelled",
    ex_expired: "Wait expired",
    ex_failed: "Needs checking",
    expertTask:
      "Expert task: {id}; see extraction / workpack state under Import.",
    expertExtra:
      "Ordinary processing already started or finished and is kept; this is an additional expert run.",
    ack: "Phone receipt: {s}",
    ack_sent: "sent",
    ack_not_sent: "confirmed not sent",
    ack_sending: "send result to check",
    ack_unknown: "send result unknown",
    ackCheck:
      "Pause the channel, check the phone, then record it. Nothing is resent and no quota is restored.",
    markSent: "Record as sent",
    markNotSent: "Record as not sent",
    testQuestion: "Test question",
    testPlaceholder: "Enter a question, or 查询[alias]: question",
    testPreview: "Test literal query (no model)",
    testModel: "Run the selected model query",
    testModelTitle: "Run one model query?",
    testModelBody:
      "Uses this channel's model quota; check the result and cost under Ask & search and Runs & cost.",
    previewDone: "Literal query done; no model and no phone message.",
    modelDone:
      "Model task started explicitly; check result and cost under Ask & search.",
    loadEvents: "Refresh message log",
    noEvents: "No message records yet.",
    eventCheck:
      "Pause the channel and check the phone first. Recording never resends.",
    phoneGot: "Phone received it",
    phoneNot: "Phone did not receive it",
    reconciled: "Recorded; nothing is resent automatically.",
    archived: "Removed channels and audit records ({n})",
    archivedRow: "{name} · removed; notes, quotas and audit are kept.",
    archivedEvents: "Show kept message log",
    legacyAudit:
      "Messages and batch audit for existing gateways stay in their original ledger.",
    steps: "Platform setup",
    stepFeishu:
      "Feishu: create a dedicated custom app in the open platform, enable the bot, grant receive-message events and send-message permission, choose long connection and publish. Don't reuse another Hermes bot's app. Save, check the connection, then start owner pairing and confirm here.",
    stepWeixin:
      "WeChat: save a standalone channel, then click scan to sign in; the QR code comes from Hermes's real sign-in API. Scan with your own account and confirm here. Old messages can't be forwarded to the bot directly; send them via another app to Feishu.",
    stepPerms:
      "Read-only queries need only query access; filing and originals are separate grants.",
    configTitle: "Channel configuration",
    basics: "Basics and credentials",
    name: "Name",
    platform: "Platform",
    appId: "App ID",
    secret: "App Secret (cleared after saving)",
    credential: "Or a local credential file (absolute path)",
    manifest: "Hermes pinned runtime manifest (absolute path)",
    access: "Identity and access",
    client: "Your access identity",
    vault: "Default vault",
    accessHelp:
      "Saving grants this identity query access on the vault, plus originals / filing as selected below.",
    original: "Allow retrieving originals",
    add: "Allow filing (not for read-only vaults)",
    answer: "Answer mode",
    evidence: "Literal evidence, no model",
    local: "Local cited draft",
    local_cloud: "Local cited draft + cloud editing",
    policyOn:
      "Uses the Models & budget configuration {sha}; cloud editing is explicit after local retrieval.",
    policyOff: "No answer model configured yet; literal queries still work.",
    cloudHint:
      "The cloud only edits the local answer and its quotations; it never reads the whole vault. Failures don't switch to a backup.",
    limits: "Quotas for this configuration revision",
    limitsHelp:
      "Pausing / restarting doesn't reset them; a new revision needs new quotas. Each 0–100.",
    query_limit: "Max incoming queries",
    model_limit: "Max model calls",
    outbound_limit: "Max phone replies",
    download_limit: "Max Feishu attachment downloads",
    expert: "Private-chat fake-expert file commands",
    expert_enabled:
      "Enable private-chat fake-expert file commands (needs add + originals access)",
    expert_limit: "Expert command limit for this revision",
    expert_receipt_limit:
      "Expert status receipt limit (separate outbound permission)",
    save: "Save configuration and grants",
    saved:
      "Configuration and grants saved. Complete owner binding, then start the channel.",
    discardConfig:
      "The channel configuration isn't saved; closing discards it. Close anyway?",
  },
});
const LIMITS = [
  "query_limit",
  "model_limit",
  "outbound_limit",
  "download_limit",
];

export function createView(ctx) {
  const api = ctx.api;
  let featureHosts = [];
  let epoch = 0,
    timer = null,
    expiry = null,
    deadline = 0,
    snapshot = null,
    clients = [],
    vaults = [],
    policy = null;
  const overviewHost = h("div.a-stats");
  const listHost = h("div.a-stack");
  const archiveHost = h("div");
  const visible = () => !document.hidden;

  async function lifecycle(entry, action) {
    const preview = await api.channelLifecyclePreview(entry.id, action);
    if (action !== "pause") {
      const ok = await confirm({
        title: action === "remove" ? t("removeTitle") : t("unbindTitle"),
        body: preview.confirmation,
        confirmLabel: action === "remove" ? t("remove") : t("unbind"),
        cancelLabel: t("keep"),
        tone: "danger",
      });
      if (!ok) return false;
    }
    await api.channelLifecycle(entry.id, {
      action,
      operation_id: crypto.randomUUID(),
      expected_revision: preview.revision,
      configuration_sha256: preview.configuration_sha256,
      expected_instance_id: preview.instance_id,
    });
    await refresh();
    toast(t("lifecycleDone"), { tone: "success" });
  }
  async function command(entry, action) {
    if (entry.origin === "legacy")
      await api.legacyChannelStart(entry.id, {
        expected_revision: entry.revision,
        configuration_sha256: entry.configuration_sha256,
      });
    else await api.channelAction(entry.id, action);
    await refresh();
  }
  function actions(entry) {
    const gui = entry.origin === "gui",
      pending =
        entry.operation &&
        ["pending", "needs_review"].includes(entry.operation.state),
      stopped = entry.runtime.process_state === "stopped",
      candidate = entry.configuration?.candidate_owner;
    const bar = h("div.a-form-actions");
    const add = (key, label, work, variant, iconName) => {
      if (["edit", "check", "bind"].includes(key) && !gui) return;
      if (key === "confirm" && !candidate) return;
      if (
        key === "resume" &&
        !(pending && entry.operation.state === "needs_review")
      )
        return;
      const disabled =
        key === "resume"
          ? false
          : pending ||
            (["edit", "check", "bind", "confirm", "start"].includes(key) &&
              !stopped) ||
            (key === "start" && (!entry.owner_id || !entry.start_supported));
      const control = button(label, { variant: variant || "small", iconName });
      control.disabled = Boolean(disabled);
      control.dataset.locked = String(Boolean(disabled));
      control.addEventListener("click", () => run(control, work));
      bar.append(control);
    };
    add(
      "edit",
      t("edit"),
      async () => openConfig(entry.configuration),
      null,
      "edit",
    );
    add("check", t("check"), () => command(entry, "check"), null, "pulse");
    add(
      "bind",
      entry.platform === "feishu" ? t("pairFeishu") : t("pairWeixin"),
      () => command(entry, entry.platform === "feishu" ? "pair" : "login"),
      null,
      "link",
    );
    add(
      "confirm",
      t("confirmOwner", { c: candidate }),
      async () => {
        await api.channelConfirmOwner(entry.id, entry.revision, candidate);
        await refresh();
      },
      "primary",
      "check",
    );
    add("start", t("start"), () => command(entry, "start"), "primary", "play");
    add("pause", t("pause"), () => lifecycle(entry, "pause"), null, "stop");
    add(
      "unbind",
      t("unbind"),
      () => lifecycle(entry, "unbind"),
      "danger-outline",
    );
    add(
      "remove",
      t("remove"),
      () => lifecycle(entry, "remove"),
      "danger-outline",
      "trash",
    );
    add("resume", t("resume"), async () => {
      await api.channelLifecycleResume(entry.id, entry.operation.id);
      await refresh();
    });
    return bar;
  }
  function pairing(entry) {
    const nodes = [];
    if (entry.configuration?.challenge)
      nodes.push(
        notice(
          t("challenge"),
          "info",
          codeBlock(entry.configuration.challenge),
        ),
      );
    if (entry.state === "login") {
      const holder = h("div");
      const show = button(t("qr"), { iconName: "eye" });
      show.addEventListener("click", () =>
        run(show, async () => {
          const source = await api.channelQr(entry.id);
          clear(
            holder,
            h("img.a-qr", {
              src: source,
              alt: t("qrAlt"),
              width: 240,
              height: 240,
            }),
          );
        }),
      );
      nodes.push(h("div.a-stack", show, holder));
    }
    return nodes;
  }
  function channelCard(entry, stale) {
    const reason = stale
      ? t("stale")
      : entry.state === "error" && entry.configuration?.connection_check?.error
        ? channelLabel(entry.configuration.connection_check.error)
        : entry.runtime.reason
          ? channelLabel(entry.runtime.reason)
          : entry.capabilities.query.reason
            ? channelLabel(entry.capabilities.query.reason)
            : "";
    const caps = ["capture", "download", "store", "model"].map((key) =>
      badge(
        `${t(key)} · ${stale ? t("refreshLater") : entry.capabilities[key].available ? t("available") : t("unavailable")}`,
        stale ? "idle" : entry.capabilities[key].available ? "success" : "idle",
      ),
    );
    const intakeLine = stale
      ? t("stale")
      : entry.intake
        ? (entry.intake.enabled
            ? t("intakeOn", { v: entry.intake.target_name || "—" })
            : t("intakeOff")) +
          (entry.intake.reason ? " · " + intakeLabel(entry.intake.reason) : "")
        : t("intakeUnset");
    const op = entry.operation
      ? `${t({ remove: "op_remove", unbind: "op_unbind", pause: "op_pause" }[entry.operation.action] || "op_other")} · ${channelLabel(entry.operation.state)}${entry.operation.reason ? " · " + channelLabel(entry.operation.reason) : ""}`
      : "";
    const detailsButton = button(t("details"), {
      variant: "ghost",
      iconName: "chevron",
      onClick: () => openDetails(entry),
    });
    const status = createChannelStatus();
    status.update(entry, stale);
    const services = createChannelServices(api, () => entry, refresh);
    services.update(entry, stale);
    const controls = featureControls(status.root, services.root);
    featureHosts.push(controls);
    return h(
      "article.a-card.a-channel",
      { dataset: { id: entry.id, stale: String(stale) } },
      h(
        "header.a-card-head",
        h(
          "div.a-detail-head",
          h("span.a-vault-icon", icon("channels")),
          h(
            "div",
            h("h2.a-card-title", entry.name),
            h(
              "p.a-card-subtitle",
              [
                entry.platform === "feishu" ? t("feishu") : t("weixin"),
                entry.origin === "legacy" ? t("legacy") : t("gui"),
                t("revision", { n: entry.revision }),
              ].join(" · "),
            ),
          ),
        ),
        badge(channelLabel(entry.state), channelTone(entry.state)),
      ),
      relationCard(entry),
      controls.node,
      reason ? notice(reason, "warning") : null,
      op ? h("p.a-subtle", op) : null,
      h("div.a-toolbar.a-caps", caps),
      h("p.a-subtle", intakeLine),
      ...pairing(entry),
      actions(entry),
      h(
        "div.a-toolbar",
        h("span.a-subtle", `${timeOnly(entry.observed_at)} · ${t("e2e")}`),
        h("span.a-spacer"),
        detailsButton,
      ),
    );
  }
  function render(overview, stale = false) {
    for (const host of featureHosts) host.dispose();
    featureHosts = [];
    snapshot = overview;
    const stats = ["feishu", "weixin"].map((platform) => {
      const entries = overview.channels.filter((c) => c.platform === platform);
      return stat(
        t(platform),
        stale
          ? "—"
          : entries.length
            ? t("connectedOf", {
                c: entries.filter((e) => e.transport === "connected").length,
                n: entries.length,
              })
            : t("notSet"),
        {
          hint: stale
            ? t("stale")
            : t("observed", { time: timeOnly(overview.observed_at) }),
        },
      );
    });
    clear(overviewHost, ...stats);
    clear(
      listHost,
      relationOverview(overview.channels),
      overview.issues.length ? notice(t("issues"), "error") : null,
      overview.channels.length
        ? ["query", "intake", "both"].flatMap((purpose) => {
            const entries = overview.channels.filter(
              (entry) => channelPurpose(entry) === purpose,
            );
            return entries.length
              ? [
                  h(
                    "h2.a-section-title",
                    lang() === "en"
                      ? {
                          query: "Knowledge queries",
                          intake: "Note saving",
                          both: "Existing mixed entries",
                        }[purpose]
                      : {
                          query: "知识库查询入口",
                          intake: "笔记保存入口",
                          both: "既有兼用入口",
                        }[purpose],
                  ),
                  ...entries.map((entry) => channelCard(entry, stale)),
                ]
              : [];
          })
        : card(
            {},
            empty(t("none"), {
              iconName: "channels",
              action: button(t("new"), {
                variant: "primary",
                onClick: () => openConfig(null),
              }),
            }),
          ),
    );
    const archived = overview.removed_channels || [];
    clear(
      archiveHost,
      archived.length
        ? disclosure(
            t("archived", { n: archived.length }),
            archived.map((item) => {
              const row = h(
                "div.a-op",
                h("p", t("archivedRow", { name: item.name })),
              );
              if (item.origin === "gui") {
                const show = button(t("archivedEvents"), { variant: "small" });
                show.addEventListener("click", () =>
                  run(show, async () => {
                    const events = await api.channelEvents(item.id);
                    row.append(codeBlock(JSON.stringify(events, null, 2)));
                    show.remove();
                  }),
                );
                row.append(show);
              } else row.append(h("p.a-subtle", t("legacyAudit")));
              return row;
            }),
          )
        : null,
    );
  }
  function stale() {
    clearTimeout(expiry);
    if (snapshot) render(snapshot, true);
  }
  function schedule(version) {
    clearTimeout(timer);
    clearTimeout(expiry);
    expiry = setTimeout(() => version === epoch && stale(), 6000);
    if (visible() && Date.now() < deadline)
      timer = setTimeout(() => observe(version), 2000);
  }
  async function observe(version) {
    if (version !== epoch || !visible() || Date.now() >= deadline) return;
    if (document.querySelector("dialog.a-sheet[open]")) {
      timer = setTimeout(() => observe(version), 2000);
      return;
    }
    try {
      const overview = await api.channelOverview();
      if (version !== epoch) return;
      render(overview);
      schedule(version);
    } catch {
      if (version !== epoch) return;
      stale();
      if (Date.now() < deadline)
        timer = setTimeout(() => observe(version), 5000);
    }
  }
  async function refresh() {
    const version = ++epoch;
    clearTimeout(timer);
    deadline = Date.now() + 180000;
    if (!snapshot) clear(listHost, skeleton(4));
    try {
      const [overview, c, v, p] = await Promise.all([
        api.channelOverview(),
        ctx.store.clients({ force: true }),
        ctx.store.vaults({ force: true }),
        api.queryPolicy(),
      ]);
      if (version !== epoch) return;
      clients = c.filter((x) => x.active);
      vaults = v.filter((x) => x.status === "active");
      policy = p;
      render(overview);
      schedule(version);
    } catch (error) {
      if (version !== epoch) return;
      if (snapshot) stale();
      else clear(listHost, failure(error, refresh));
    }
  }

  function openConfig(existing) {
    const s = existing?.settings || {};
    const en = lang() === "en";
    const purpose = select(
      [
        ...(existing && !s.purpose
          ? [["", en ? "Existing mixed entry" : "兼用 · 保留现有绑定"]]
          : []),
        ["query", en ? "Knowledge query" : "知识库查询"],
        ["intake", en ? "Note saving (Feishu)" : "笔记保存（飞书）"],
      ],
      s.purpose || (existing ? "" : "query"),
    );
    const natural = checkbox(
      en ? "Allow natural-language questions" : "允许自然语言提问",
      !!s.natural_questions,
    );
    const expertMedia = select(
      [
        ["unknown", en ? "Not selected" : "尚未选择"],
        ["text", en ? "Text" : "文字"],
        ["images", en ? "Text and images" : "文字与图片"],
        ["full", en ? "Full material" : "完整资料"],
      ],
      s.expert_media || "unknown",
    );
    const name = input({ required: true, maxLength: 100, value: s.name || "" });
    const platform = select(
      [
        ["feishu", t("feishu")],
        ["weixin", t("weixin")],
      ],
      s.platform || "feishu",
    );
    const appId = input({ pattern: "cli_[A-Za-z0-9]+", value: s.app_id || "" });
    const secret = input({ type: "password", autocomplete: "new-password" });
    const credential = input({ placeholder: "/path/to/feishu_bot.txt" });
    const manifest = input({
      required: true,
      placeholder: "/path/to/runtime-manifest.json",
      value: s.runtime_manifest || "",
    });
    const client = select(
      clients.map((c) => [c.id, c.name]),
      s.client_id,
    );
    const vault = select(
      vaults.map((v) => [v.id, v.name]),
      s.vault_id,
    );
    const scopeEditor = createQueryScopeEditor(s);
    scopeEditor.setVaults(
      vaults.filter(
        (v) =>
          v.status === "active" &&
          (!v.attachment_state || v.attachment_state === "attached"),
      ),
    );
    const original = checkbox(t("original"), !!s.original);
    const add = checkbox(t("add"), !!s.add);
    const mode = select(
      [
        ["model", en ? "Use the default model" : "使用当前默认模型"],
        ["evidence", t("evidence")],
        ["local", t("local")],
        ["local_cloud", t("local_cloud")],
      ],
      s.answer_mode || "model",
    );
    const limits = Object.fromEntries(
      LIMITS.map((key) => [
        key,
        input({
          type: "number",
          min: 0,
          max: 100,
          required: true,
          value: s[key] ?? 0,
        }),
      ]),
    );
    const expertEnabled = checkbox(t("expert_enabled"), !!s.expert_enabled);
    const expertLimit = input({
      type: "number",
      min: 0,
      max: 100,
      value: s.expert_limit ?? 0,
    });
    const receiptLimit = input({
      type: "number",
      min: 0,
      max: 100,
      value: s.expert_receipt_limit ?? 0,
    });
    const feishuFields = h(
      "div.a-form-grid",
      field(t("appId"), appId),
      field(t("secret"), secret),
      field(t("credential"), credential, { optional: true }),
    );
    const cloudHint = h("p.a-subtle", t("cloudHint"));
    const layout = () => {
      const feishu = platform.value === "feishu";
      if (!feishu) purpose.value = "query";
      if (purpose.value) {
        add.input.checked = purpose.value === "intake";
        add.input.disabled = true;
        if (purpose.value === "query") expertEnabled.input.checked = false;
        if (purpose.value === "intake") mode.value = "evidence";
      } else add.input.disabled = false;
      expertEnabled.input.disabled = purpose.value === "query";
      mode.disabled = purpose.value === "intake";
      natural.input.disabled = purpose.value === "intake";
      scopeEditor.context(!add.input.checked, vault, original.input);
      const intakeOption = [...purpose.options].find(
        (o) => o.value === "intake",
      );
      if (intakeOption) intakeOption.disabled = !feishu;
      feishuFields.hidden = !feishu;
      appId.required = feishu;
      limits.model_limit.disabled = mode.value === "evidence";
      cloudHint.hidden = mode.value !== "local_cloud";
    };
    scopeEditor.root.addEventListener("change", layout);
    add.input.addEventListener("change", layout);
    purpose.addEventListener("change", layout);
    platform.addEventListener("change", layout);
    mode.addEventListener("change", layout);
    const submit = button(t("save"), { variant: "primary", iconName: "check" });
    const form = h(
      "form.a-form",
      {
        dataset: { draft: "tracked" },
        onsubmit: (event) => event.preventDefault(),
      },
      h(
        "fieldset.a-fieldset",
        h("legend", t("basics")),
        h(
          "div.a-form-grid",
          field(t("name"), name),
          field(t("platform"), platform),
          field(en ? "Purpose" : "入口用途", purpose),
        ),
        feishuFields,
        field(t("manifest"), manifest),
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("access")),
        h(
          "div.a-form-grid",
          field(t("client"), client),
          field(t("vault"), vault),
        ),
        h("p.a-subtle", t("accessHelp")),
        h("div.a-checks", original.label, add.label),
        scopeEditor.root,
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("answer")),
        field(t("answer"), mode),
        natural.label,
        h(
          "p.a-subtle",
          policy?.configured
            ? t("policyOn", {
                sha: (policy.configuration_sha256 || "—").slice(0, 12),
              })
            : t("policyOff"),
        ),
        cloudHint,
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("limits")),
        h("p.a-subtle", t("limitsHelp")),
        h(
          "div.a-form-grid",
          LIMITS.map((key) => field(t(key), limits[key])),
        ),
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("expert")),
        expertEnabled.label,
        field(en ? "Expert material output" : "专家资料输出范围", expertMedia),
        h(
          "div.a-form-grid",
          field(t("expert_limit"), expertLimit),
          field(t("expert_receipt_limit"), receiptLimit),
        ),
      ),
      h("div.a-form-actions", submit),
    );
    const limitFields = [...form.querySelectorAll("fieldset")].find(
      (node) => node.querySelector("legend")?.textContent === t("limits"),
    );
    if (limitFields && (s.policy_version || 2) === 2) limitFields.hidden = true;
    layout();
    const view = sheet({
      title: t("configTitle"),
      subtitle: existing?.settings?.name,
      wide: true,
      onClose: () => void refresh(),
      beforeClose: () =>
        !hasUnsavedChanges(form) || window.confirm(t("discardConfig")),
    });
    view.body.append(form);
    rememberDraft(form);
    submit.addEventListener("click", () =>
      run(submit, async () => {
        if (!form.reportValidity()) return;
        const acknowledge = captureDraftSave(form);
        await api.channelSave({
          id: existing?.id || null,
          expected_revision: existing?.revision || null,
          settings: {
            policy_version: s.policy_version || 2,
            ...(purpose.value ? { purpose: purpose.value } : {}),
            natural_questions: natural.input.checked,
            ...(expertMedia.value !== "unknown" ||
            Object.hasOwn(s, "expert_media")
              ? { expert_media: expertMedia.value }
              : {}),
            name: name.value.trim(),
            platform: platform.value,
            app_id: platform.value === "feishu" ? appId.value.trim() : null,
            client_id: client.value,
            vault_id: vault.value,
            ...scopeEditor.settings(),
            add: add.input.checked,
            original: original.input.checked,
            answer_mode: mode.value,
            configuration_sha256:
              existing?.settings?.configuration_sha256 ??
              policy?.configuration_sha256 ??
              null,
            runtime_manifest: manifest.value.trim(),
            ...Object.fromEntries(
              LIMITS.map((key) => [key, Number(limits[key].value)]),
            ),
            expert_enabled: expertEnabled.input.checked,
            expert_limit: Number(expertLimit.value),
            expert_receipt_limit: Number(receiptLimit.value),
          },
          credential_file: credential.value.trim() || null,
          app_secret: secret.value || null,
        });
        secret.value = "";
        credential.value = "";
        acknowledge();
        ctx.store.invalidate("clients");
        toast(t("saved"), { tone: "success" });
        view.close();
      }),
    );
  }

  function openDetails(entry) {
    let tab = "intake";
    const intake = createIntake(ctx, () => entry, refresh);
    const tabHost = h("div");
    const body = h("div.a-stack");
    const view = sheet({
      title: entry.name,
      subtitle: entry.platform === "feishu" ? t("feishu") : t("weixin"),
      wide: true,
      onClose: () => void refresh(),
      beforeClose: () =>
        !(intake.dirty() || hasUnsavedChanges(body)) ||
        window.confirm(t("discardConfig")),
    });
    view.body.append(tabHost, body);
    function draw() {
      const available = [
        "intake",
        "expert",
        ...(entry.origin === "gui" ? ["test", "events"] : []),
        "diag",
      ];
      clear(
        tabHost,
        tabs(
          available.map((value) => ({ value, label: t("tab_" + value) })),
          tab,
          (value) => ((tab = value), draw()),
          entry.name,
        ),
      );
      if (tab === "intake") {
        clear(body, intake.node);
        void intake.refresh();
      } else if (tab === "expert") void expertTab();
      else if (tab === "test") testTab();
      else if (tab === "events") void eventsTab();
      else
        clear(
          body,
          codeBlock(
            JSON.stringify(
              {
                platform: entry.platform,
                app_id: entry.app_id,
                owner_id: entry.owner_id,
                runtime: entry.runtime,
                capabilities: entry.capabilities,
                remaining: entry.remaining,
                unresolved_deliveries: entry.unresolved_deliveries,
                operation: entry.operation,
                connection_check: entry.configuration?.connection_check,
              },
              null,
              2,
            ),
          ),
        );
    }
    async function expertTab() {
      if (
        entry.settings?.platform !== "feishu" &&
        entry.platform !== "feishu"
      ) {
        clear(body, notice(t("expertOnlyFeishu"), "info"));
        return;
      }
      clear(body, skeleton(3));
      try {
        const rows = await api.channelExpertIntents(entry.id);
        clear(
          body,
          rows.length
            ? rows.map((item) => {
                const node = h(
                  "div.a-op",
                  h(
                    "strong",
                    t("ex_" + item.state) === "ex_" + item.state
                      ? t("ex_failed")
                      : t("ex_" + item.state),
                  ),
                );
                if (item.reason) node.append(h("p.a-subtle", item.reason));
                if (item.task_id)
                  node.append(
                    h("p.a-subtle", t("expertTask", { id: item.task_id })),
                  );
                if (
                  item.ordinary_requests.some(
                    (r) => r.scope === "existing_retained_extra_expert",
                  )
                )
                  node.append(h("p.a-subtle", t("expertExtra")));
                if (item.ack) {
                  node.append(
                    h(
                      "p",
                      t("ack", {
                        s:
                          t("ack_" + item.ack.state) === "ack_" + item.ack.state
                            ? item.ack.state
                            : t("ack_" + item.ack.state),
                      }),
                    ),
                  );
                  if (["sending", "unknown"].includes(item.ack.state)) {
                    node.append(notice(t("ackCheck"), "warning"));
                    const bar = h("div.a-toolbar");
                    for (const [label, result] of [
                      [t("markSent"), "sent"],
                      [t("markNotSent"), "not_sent"],
                    ]) {
                      const control = button(label, { variant: "small" });
                      control.addEventListener("click", () =>
                        run(control, async () => {
                          await api.channelExpertReconcile(
                            entry.id,
                            item.event_id,
                            {
                              for_state: item.ack.for_state,
                              acknowledgement: result,
                            },
                          );
                          await expertTab();
                        }),
                      );
                      bar.append(control);
                    }
                    node.append(bar);
                  }
                }
                return node;
              })
            : h("p.a-subtle", t("expertNone")),
        );
      } catch (error) {
        clear(body, failure(error, expertTab));
      }
    }
    function testTab() {
      const question = input({
        required: true,
        maxLength: 2000,
        placeholder: t("testPlaceholder"),
      });
      const out = h("div");
      const blocked = Boolean(
        entry.operation &&
        ["pending", "needs_review"].includes(entry.operation.state),
      );
      const preview = button(t("testPreview"), { iconName: "search" });
      const model = button(t("testModel"), {
        variant: "primary",
        iconName: "spark",
      });
      preview.disabled = blocked;
      model.disabled =
        blocked || entry.configuration?.settings.answer_mode === "evidence";
      const form = h(
        "form.a-form",
        {
          dataset: { draft: "tracked" },
          onsubmit: (event) => event.preventDefault(),
        },
        field(t("testQuestion"), question),
        h("div.a-form-actions", preview, model),
        out,
      );
      for (const [control, execute] of [
        [preview, false],
        [model, true],
      ])
        control.addEventListener("click", () =>
          run(control, async () => {
            if (!form.reportValidity()) return;
            if (
              execute &&
              !(await confirm({
                title: t("testModelTitle"),
                body: t("testModelBody"),
                confirmLabel: t("testModel"),
              }))
            )
              return false;
            const result = await api.channelTest(
              entry.id,
              question.value,
              execute,
            );
            clear(out, codeBlock(JSON.stringify(result, null, 2)));
            toast(execute ? t("modelDone") : t("previewDone"), {
              tone: "success",
            });
          }),
        );
      clear(body, form);
    }
    async function eventsTab() {
      const list = h("div.a-stack");
      const load = button(t("loadEvents"), {
        variant: "small",
        iconName: "refresh",
      });
      const fill = async () => {
        const events = await api.channelEvents(entry.id);
        clear(
          list,
          events.length
            ? events.map((event) => {
                const node = h(
                  "div.a-op",
                  h("p", h("code", event.event_id), " · ", event.state),
                );
                if (["unknown", "sending"].includes(event.state)) {
                  node.append(notice(t("eventCheck"), "warning"));
                  const bar = h("div.a-toolbar");
                  for (const [label, ack] of [
                    [t("phoneGot"), "sent"],
                    [t("phoneNot"), "not_sent"],
                  ]) {
                    const control = button(label, { variant: "small" });
                    control.addEventListener("click", () =>
                      run(control, async () => {
                        await api.channelReconcile(
                          entry.id,
                          event.event_id,
                          ack,
                        );
                        toast(t("reconciled"), { tone: "success" });
                        await fill();
                      }),
                    );
                    bar.append(control);
                  }
                  node.append(bar);
                }
                return node;
              })
            : h("p.a-subtle", t("noEvents")),
        );
      };
      load.addEventListener("click", () => run(load, fill));
      clear(body, load, list);
      await run(load, fill);
    }
    draw();
  }
  document.addEventListener("visibilitychange", onVisibility);
  function onVisibility() {
    if (!visible()) {
      clearTimeout(timer);
      stale();
    } else if (snapshot) refresh();
  }
  return {
    node: h(
      "div.a-stack",
      overviewHost,
      listHost,
      archiveHost,
      disclosure(
        t("steps"),
        h("p", t("stepFeishu")),
        h("p", t("stepWeixin")),
        h("p.a-subtle", t("stepPerms")),
      ),
    ),
    header: {
      actions: [
        button(t("new"), {
          variant: "primary",
          iconName: "plus",
          onClick: () => openConfig(null),
        }),
      ],
    },
    refresh,
    dispose() {
      epoch++;
      clearTimeout(timer);
      clearTimeout(expiry);
      for (const host of featureHosts) host.dispose();
      document.removeEventListener("visibilitychange", onVisibility);
    },
  };
}
