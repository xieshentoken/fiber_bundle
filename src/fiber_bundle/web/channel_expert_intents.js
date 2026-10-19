import { phrase, setText, uiText } from "./i18n/index.js";
import { el } from "./ui.js";
import { control } from "./record_ui.js";
const states = {
  waiting_file: "等待本私聊上传 PDF",
  needs_selection: "等待选择文件",
  awaiting_download: "等待文件下载",
  bound: "来源已绑定",
  queued: "专家任务待执行",
  submitted: "专家任务已提交",
  cancelled: "意图已取消",
  expired: "等待已到期",
  failed: "需核对",
};
export function expertIntents(api, channel) {
  const root = el("details"),
    list = el("div");
  root.append(el("summary", uiText("fake-expert 文件意图与回执")));
  async function refresh() {
    const value = channel();
    if (value.settings.platform !== "feishu") {
      setText(list, uiText("此入口不支持飞书专家指令。"));
      return;
    }
    const rows = await api.channelExpertIntents(value.id);
    if (!root.isConnected || channel().id !== value.id) return;
    list.replaceChildren();
    for (const item of rows) {
      const line = el("article");
      line.append(el("p", uiText(states[item.state] || "需核对")));
      if (item.reason) line.append(el("p", uiText(item.reason)));
      if (item.task_id)
        line.append(
          el(
            "p",
            phrase(
              "专家任务：{p0}；在资料导入中查看提取/补充 workpack 状态。",
              { p0: item.task_id },
            ),
          ),
        );
      if (
        item.ordinary_requests.some(
          (r) => r.scope === "existing_retained_extra_expert",
        )
      )
        line.append(
          el(
            "p",
            uiText("普通处理已经开始或完成，原结果保留；本次为额外专家处理。"),
          ),
        );
      if (item.ack) {
        line.append(
          el(
            "p",
            phrase("手机回执：{p0}", {
              p0:
                {
                  sent: "已发送",
                  not_sent: "人工核实未发送",
                  sending: "发送结果待核对",
                  unknown: "发送结果未知",
                }[item.ack.state] || "待核对",
            }),
          ),
        );
        if (["sending", "unknown"].includes(item.ack.state)) {
          line.append(
            el(
              "p",
              uiText("先暂停入口，在手机核实后登记；不会重发或恢复额度。"),
            ),
          );
          for (const [title, result] of [
            ["登记已发送", "sent"],
            ["登记未发送", "not_sent"],
          ])
            line.append(
              control(title, async () => {
                await api.channelExpertReconcile(value.id, item.event_id, {
                  for_state: item.ack.for_state,
                  acknowledgement: result,
                });
                await refresh();
              }),
            );
        }
      }
      list.append(line);
    }
    if (!rows.length) list.append(el("p", uiText("尚无明确专家文件指令。")));
  }
  root.append(control("刷新专家意图", refresh), list);
  return root;
}
