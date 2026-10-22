// Backend & reminders. The desktop window and backend run independently; stopping and
// reminder preferences are explicit. Component checks never go online or read credentials.
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  codeBlock,
  copyable,
  disclosure,
  empty,
  failure,
  listRow,
  notice,
  skeleton,
  stat,
  table,
  toggle,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import { number, timeOnly } from "./format.js";

const t = strings({
  zh: {
    running: "管理后台运行中",
    state: "管理后台：{s}",
    managed: "受管实例：可独立停止这份后台；关闭管理窗口会保留后台。",
    external: "此后台未由桌面生命周期命令启动。",
    channels: "消息入口",
    channelsValue: "{c} / {n} 已连接",
    observed: "观测于 {time}",
    openChannels: "查看飞书 / 微信与 Hermes 状态",
    interactive: "交互任务排队",
    heavy: "重任务排队",
    notRecorded: "未记录",
    queueRaw: "队列原始记录",
    digests: "每日摘要",
    sent: "已发送",
    unknownSend: "发送结果未知，请人工核对；不会自动重发",
    items: "{n} 项",
    stop: "停止管理后台",
    stopTitle: "停止这份管理后台？",
    stopBody1:
      "停止这份管理后台，并取消受控查询的后续处理。已发生费用和原件会保留。",
    stopBody2: "独立微信接收进程须另行管理。",
    stopping: "后台正在停止。在本机重新启动后，刷新本页重新连接。",
    components: "组件与版本",
    version: "当前安装版本 {p} · 候选 schema {s}",
    immutable: "执行配置采用不可变版本；保存更新仅供新任务选用。",
    name: "组件",
    revision: "版本",
    digest: "SHA-256",
    fixed: "固定来源",
    optional: "可选组件",
    optionalNote:
      "基础存储、界面与备份可独立使用。可选组件状态不代表账号已连接。",
    hermesMessages: "Hermes 消息组件",
    hermesAgent: "Hermes Agent",
    docker: "Docker",
    installed: "已安装并核对文件；连接需单独验证",
    invalid: "文件校验失败，请重新核对组件",
    missing: "尚未安装或选择",
    dockerMissing: "未找到 Docker；依赖它的提取与图谱任务需另行安装。",
    dockerFound: "已找到 Docker 命令；服务与镜像尚未验证。",
    modelsNote:
      "OCR / ASR 和本地 / 云模型需明确配置；此检查不读取凭据、连接账号或启动下载。",
    check: "检查已安装组件版本",
    matched: "已安装代码与固定来源摘要一致。",
    changed: "检测到组件变化，请保留任务并核对候选版本。",
    checkNote: "此检查不联网、不核验本机 ASR 权重；运行任务仍使用原绑定。",
    reminders: "批次提醒",
    remindersNote: "仅待确认批次进入每日摘要；关闭提醒不删除待办。",
    noReminders: "暂无待确认批次。",
    reminder: "每日批次提醒",
    batch: "{n} 条资料 · {s}",
    saved: "提醒偏好已保存。",
  },
  en: {
    running: "Admin backend running",
    state: "Admin backend: {s}",
    managed:
      "Managed instance: you can stop this backend on its own; closing the window keeps it running.",
    external: "This backend wasn't started by the desktop lifecycle command.",
    channels: "Message channels",
    channelsValue: "{c} / {n} connected",
    observed: "Observed {time}",
    openChannels: "Open Feishu / WeChat and Hermes status",
    interactive: "Interactive queue",
    heavy: "Heavy-task queue",
    notRecorded: "not recorded",
    queueRaw: "Raw queue record",
    digests: "Daily digests",
    sent: "Sent",
    unknownSend: "Delivery unknown — check by hand; never resent automatically",
    items: "{n} items",
    stop: "Stop admin backend",
    stopTitle: "Stop this admin backend?",
    stopBody1:
      "Stops this admin backend and cancels further processing of managed queries. Costs incurred and originals are kept.",
    stopBody2: "A standalone WeChat receiver must be managed separately.",
    stopping:
      "The backend is stopping. Start it again on this computer, then refresh to reconnect.",
    components: "Components & versions",
    version: "Installed version {p} · candidate schema {s}",
    immutable:
      "Execution configurations are immutable; saved updates only apply to new tasks.",
    name: "Component",
    revision: "Revision",
    digest: "SHA-256",
    fixed: "pinned source",
    optional: "Optional components",
    optionalNote:
      "Core storage, UI and backups work on their own. Optional component state doesn't mean an account is connected.",
    hermesMessages: "Hermes messaging",
    hermesAgent: "Hermes Agent",
    docker: "Docker",
    installed: "Installed and files checked; connection verified separately",
    invalid: "File check failed — re-check the component",
    missing: "Not installed or selected",
    dockerMissing:
      "Docker not found; extraction and graph tasks that need it must be installed separately.",
    dockerFound: "Docker command found; service and images not verified.",
    modelsNote:
      "OCR / ASR and local / cloud models need explicit configuration; this check reads no credentials, connects no accounts and downloads nothing.",
    check: "Check installed component versions",
    matched: "Installed code matches the pinned source digests.",
    changed:
      "Component changes detected. Keep tasks and check the candidate version.",
    checkNote:
      "This check is offline and doesn't verify local ASR weights; running tasks keep their original binding.",
    reminders: "Batch reminders",
    remindersNote:
      "Only batches waiting for confirmation go into the daily digest; turning a reminder off never deletes the to-do.",
    noReminders: "No batches waiting.",
    reminder: "Daily batch reminder",
    batch: "{n} items · {s}",
    saved: "Reminder preference saved.",
  },
});
const installedText = (state) =>
  state === "installed_integrity_checked" ||
  state === "selected_integrity_checked"
    ? t("installed")
    : state === "invalid"
      ? t("invalid")
      : t("missing");
const installedTone = (state) =>
  state?.endsWith("integrity_checked")
    ? "success"
    : state === "invalid"
      ? "error"
      : "idle";

export function createView(ctx) {
  const root = h("div.a-stack");
  let epoch = 0,
    instance = null;
  async function refresh() {
    const mine = ++epoch;
    if (!root.childElementCount) clear(root, skeleton(6));
    let status, components, reminders, channels;
    try {
      [status, components, reminders, channels] = await Promise.all([
        ctx.api.backend(),
        ctx.store.components({ force: true }),
        ctx.api.reminders(),
        ctx.api.channelOverview(),
      ]);
    } catch (error) {
      if (mine === epoch) clear(root, failure(error, refresh));
      return;
    }
    if (mine !== epoch) return;
    instance = status;
    const stop = button(t("stop"), {
      variant: "danger-outline",
      iconName: "stop",
    });
    stop.disabled = !status.managed || status.state !== "running";
    stop.addEventListener("click", () =>
      run(stop, async () => {
        if (!instance?.managed) return false;
        if (
          !(await confirm({
            title: t("stopTitle"),
            body: [t("stopBody1"), t("stopBody2")],
            confirmLabel: t("stop"),
            tone: "danger",
          }))
        )
          return false;
        await ctx.api.stopBackend(instance.instance_id);
        instance = null;
        stop.dataset.locked = "true";
        clear(root, notice(t("stopping"), "warning"));
      }),
    );
    const queueValue = (n) =>
      Number.isInteger(n) ? number(n) : t("notRecorded");
    const connected = channels.channels.filter(
      (e) => e.transport === "connected",
    ).length;
    const cap = components.capabilities;
    const check = button(t("check"), { iconName: "shield" });
    const checkHost = h("div.a-stack");
    check.addEventListener("click", () =>
      run(check, async () => {
        const result = await ctx.api.checkComponents();
        if (mine !== epoch) return;
        clear(
          checkHost,
          notice(
            result.state === "matched" ? t("matched") : t("changed"),
            result.state === "matched" ? "success" : "warning",
            h("p.a-subtle", t("checkNote")),
          ),
          disclosure("JSON", codeBlock(JSON.stringify(result, null, 2))),
        );
      }),
    );
    clear(
      root,
      card(
        {
          title:
            status.state === "running"
              ? t("running")
              : t("state", { s: status.state }),
          subtitle: status.managed ? t("managed") : t("external"),
          actions: h(
            "div.a-toolbar",
            badge(
              status.state,
              status.state === "running" ? "success" : "paused",
            ),
            stop,
          ),
        },
        h(
          "div.a-stats",
          stat(t("interactive"), queueValue(status.queue?.interactive_waiting)),
          stat(t("heavy"), queueValue(status.queue?.heavy_waiting)),
          stat(
            t("channels"),
            t("channelsValue", { c: connected, n: channels.channels.length }),
            {
              hint: t("observed", { time: timeOnly(channels.observed_at) }),
              onClick: () => ctx.navigate("channels"),
            },
          ),
        ),
        disclosure(
          t("queueRaw"),
          codeBlock(JSON.stringify(status.queue, null, 2)),
        ),
        status.reminder_history?.length
          ? disclosure(
              t("digests"),
              h(
                "div.a-rows",
                status.reminder_history.map((d) =>
                  listRow({
                    iconName: "clock",
                    title: d.id,
                    detail: t("items", { n: d.batch_count }),
                    status:
                      d.state === "sent"
                        ? [t("sent"), "success"]
                        : [t("unknownSend"), "error"],
                  }),
                ),
              ),
            )
          : null,
      ),
      card(
        {
          title: t("components"),
          subtitle: t("version", {
            p: components.package,
            s: components.candidate_schema,
          }),
          actions: check,
        },
        h("p.a-subtle", t("immutable")),
        table(
          [
            { label: t("name"), value: (c) => c.name },
            { label: t("revision"), value: (c) => c.revision || t("fixed") },
            {
              label: t("digest"),
              value: (c) =>
                copyable(c.sha256, { display: c.sha256.slice(0, 12) }),
            },
          ],
          components.components,
        ),
        checkHost,
        cap
          ? h(
              "div.a-stack",
              h("h3", t("optional")),
              h("p.a-subtle", t("optionalNote")),
              h(
                "div.a-rows",
                listRow({
                  iconName: "channels",
                  title: t("hermesMessages"),
                  status: [
                    installedText(cap.hermes_messages.state),
                    installedTone(cap.hermes_messages.state),
                  ],
                }),
                listRow({
                  iconName: "clients",
                  title: t("hermesAgent"),
                  status: [
                    installedText(cap.hermes_agent.state),
                    installedTone(cap.hermes_agent.state),
                  ],
                }),
                listRow({
                  iconName: "layers",
                  title: t("docker"),
                  status:
                    cap.docker_extraction_graph.state === "missing"
                      ? [t("dockerMissing"), "idle"]
                      : [t("dockerFound"), "waiting"],
                }),
              ),
              h("p.a-subtle", t("modelsNote")),
            )
          : null,
      ),
      card(
        {
          title: t("reminders"),
          subtitle: t("remindersNote"),
          className: "is-flush",
        },
        reminders.length
          ? h(
              "div.a-rows",
              reminders.map((batch) => {
                const control = toggle(t("reminder"), batch.reminder_enabled, {
                  dataset: { draft: "ignore" },
                });
                control.input.addEventListener("change", async () => {
                  control.input.disabled = true;
                  try {
                    await ctx.api.setReminder(batch.id, control.input.checked);
                    toast(t("saved"), { tone: "success" });
                  } catch (error) {
                    control.input.checked = !control.input.checked;
                    toast(error.message, { tone: "error" });
                  } finally {
                    control.input.disabled = false;
                  }
                });
                return listRow({
                  iconName: "batch",
                  title: batch.id,
                  detail: t("batch", { n: batch.count, s: batch.state }),
                  actions: [control.label],
                });
              }),
            )
          : empty(t("noReminders"), { iconName: "clock" }),
      ),
    );
  }
  return { node: root, refresh, dispose: () => epoch++ };
}
