import { phrase, uiText } from "./i18n/index.js";
// Backend lifecycle and notification preferences are regular authenticated API operations.
import { $, action, el, checkbox } from "./ui.js";
import { control, showJSON } from "./record_ui.js";
import { confirmAction } from "./confirm_dialog.js";
export function createBackendView(api) {
  let epoch = 0,
    instance = null;
  async function refresh() {
    const revision = ++epoch;
    const [status, components, reminders, channels] = await Promise.all([
      api.backend(),
      api.components(),
      api.reminders(),
      api.channelOverview(),
    ]);
    if (revision !== epoch) return;
    instance = status;
    const root = $("backend-content");
    root.replaceChildren(
      el(
        "h3",
        status.state === "running"
          ? uiText("管理后台运行中")
          : phrase("管理后台：{p0}", { p0: uiText(status.state) }),
      ),
      el(
        "p",
        status.managed
          ? uiText("可独立停止这份后台；关闭管理窗口会保留后台。")
          : uiText("此后台未由桌面生命周期命令启动。"),
      ),
      el(
        "p",
        phrase("项目消息入口：{p0} / {p1} 个平台连接已建立。观测时间 {p2}。", {
          p0: channels.channels.filter(
            (entry) => entry.transport === "connected",
          ).length,
          p1: channels.channels.length,
          p2: new Date(channels.observed_at * 1000).toLocaleTimeString(),
        }),
      ),
      control("查看飞书 / 微信与 Hermes 状态", async () => {
        document.querySelector('[data-tab="channels"]')?.click();
      }),
    );
    const queue = el("div", null, "ui-facts");
    for (const [label, count] of [
      ["交互任务排队", status.queue?.interactive_waiting],
      ["重任务排队", status.queue?.heavy_waiting],
    ]) {
      const item = el("div", null, "ui-fact");
      item.append(
        el("strong", uiText(label)),
        el("small", ""),
        el(
          "span",
          Number.isInteger(count)
            ? phrase("{p0} 个", { p0: count })
            : uiText("未记录"),
          "status-badge",
        ),
      );
      queue.append(item);
    }
    root.append(queue);
    showJSON(root, status.queue);
    for (const digest of status.reminder_history || []) {
      root.append(
        el(
          "p",
          phrase("每日摘要 {p0} · {p1} · {p2} 项", {
            p0: digest.id,
            p1:
              digest.state === "sent"
                ? uiText("已发送")
                : uiText("发送结果未知，请人工核对；不会自动重发"),
            p2: digest.batch_count,
          }),
        ),
      );
    }
    $("backend-stop").dataset.policyDisabled = String(
      !status.managed || status.state !== "running",
    );
    $("backend-stop").disabled = !status.managed || status.state !== "running";
    const version = $("components-content");
    version.replaceChildren(
      el(
        "p",
        phrase("当前安装版本 {p0} · 候选 schema {p1}", {
          p0: components.package,
          p1: components.candidate_schema,
        }),
      ),
      el("p", uiText("执行配置采用不可变版本；保存更新仅供新任务选用。")),
    );
    const details = el("details", null, "component-details");
    details.append(el("summary", uiText("查看组件版本与校验摘要")));
    version.append(details);
    for (const component of components.components)
      details.append(
        el(
          "p",
          uiText(
            component.name +
              " · " +
              (component.revision || "固定来源") +
              " · " +
              component.sha256,
          ),
        ),
      );
    version.append(
      control("检查已安装组件版本", async () => {
        const result = await api.checkComponents();
        if (revision !== epoch) return;
        version.querySelector(".version-check")?.remove();
        const check = el("div", null, "version-check");
        check.append(
          el(
            "p",
            result.state === "matched"
              ? uiText("已安装代码与固定来源摘要一致。")
              : uiText("检测到组件变化，请保留任务并核对候选版本。"),
          ),
        );
        check.append(
          el(
            "p",
            uiText(
              "此检查不联网、不核验本机 ASR 权重；版本安装由独立发行步骤处理，运行任务仍使用原绑定。",
            ),
          ),
        );
        showJSON(check, result);
        version.append(check);
      }),
    );
    const list = $("reminders-content");
    list.replaceChildren(
      el("p", uiText("仅待确认批次进入每日摘要；关闭提醒不删除待办。")),
    );
    if (!reminders.length) list.append(el("p", uiText("暂无待确认批次。")));
    for (const batch of reminders) {
      const card = el("article", null, "row"),
        box = checkbox("每日批次提醒", batch.reminder_enabled);
      card.append(
        el("h3", batch.id),
        el(
          "p",
          phrase("{p0} 条资料 · {p1}", {
            p0: batch.count,
            p1: uiText(batch.state),
          }),
        ),
        box.label,
        control("保存提醒偏好", async (notify) => {
          await api.setReminder(batch.id, box.input.checked);
          await refresh();
          notify("提醒偏好已保存。");
        }),
      );
      list.append(card);
    }
  }
  action($("backend-refresh"), refresh);
  action($("backend-stop"), async () => {
    if (
      !instance?.managed ||
      !(await confirmAction({
        title: "停止这份管理后台？",
        body: [
          "停止这份管理后台，并取消受控查询的后续处理。已发生费用和原件会保留。",
          "独立微信接收进程须另行管理。",
        ],
        confirmLabel: "停止管理后台",
        tone: "danger",
      }))
    )
      return;
    await api.stopBackend(instance.instance_id);
    $("backend-content").replaceChildren(
      el("p", uiText("后台正在停止。可在本机启动后使用顶部“重新连接”。")),
    );
    instance = null;

    $("backend-stop").dataset.policyDisabled = "true";
    $("backend-stop").disabled = true;
  });
  return {
    refresh,
    clear() {
      epoch++;
      instance = null;
      for (const id of [
        "backend-content",
        "components-content",
        "reminders-content",
      ])
        $(id).replaceChildren();
    },
  };
}
