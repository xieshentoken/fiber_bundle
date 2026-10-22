import { relationCard } from "./channel_query_scope.js";
import { createChannelServices } from "./channel_services.js";
import { phrase, setText, uiText } from "./i18n/index.js";
import { expertIntents } from "./channel_expert_intents.js";
import { el, action } from "./ui.js";
import { control, showJSON } from "./record_ui.js";
import { createChannelStatus, channelLabel } from "./channel_status.js";
import { createChannelActions } from "./channel_actions.js";
import { createChannelIntake } from "./channel_intake.js";
import { intakeLabel } from "./channel_intake_records.js";
import { captureDraftSave } from "./unsaved_changes.js";
import { disclosure } from "./view_sections.js";

export function createChannelCard(api, initial, refresh, edit) {
  let entry = initial;
  const root = el("article", null, "record-card channel-card");
  root.dataset.channelId = entry.id;
  root.dataset.taskKind = "channel";
  root.dataset.taskId = entry.id;
  const relation = el("div");
  root.append(relation);
  const title = el("h3"),
    meta = el("p", null, "muted"),
    status = createChannelStatus(),
    pairing = el("div"),
    diagnostics = el("details"),
    diagnosticBody = el("pre"),
    management = createChannelActions(api, () => entry, refresh, edit);
  diagnostics.append(el("summary", uiText("连接诊断详情")), diagnosticBody);
  root.append(title, meta, status.root, management.root, pairing, diagnostics);
  const services = createChannelServices(api, () => entry, refresh);
  root.append(services.root);
  const intakeSummary = el("p", null, "muted"),
    intake = createChannelIntake(api, () => entry, refresh);
  root.append(
    intakeSummary,
    disclosure("自动入库与资料处理记录", intake.root),
    disclosure(
      "专家提取指令",
      expertIntents(api, () => entry),
    ),
  );
  const queryDetails = el("details"),
    form = el("form", null, "create-form"),
    input = el("input"),
    label = el("label", uiText("测试问题")),
    preview = el("button", uiText("测试原文查询（零模型调用）")),
    model = el("button", uiText("明确执行所选模型查询"));
  queryDetails.append(el("summary", uiText("指定范围查询与模型测试")));
  form.dataset.draft = "tracked";
  form.addEventListener("submit", (event) => event.preventDefault());
  input.required = true;
  input.maxLength = 2000;
  setText(input, uiText("输入问题，或查询[库别名]：问题"), "placeholder");
  label.append(input);
  form.append(label, preview, model);
  for (const [button, execute] of [
    [preview, false],
    [model, true],
  ]) {
    button.type = "button";
    action(button, async (notify) => {
      if (!form.reportValidity()) return;
      const acknowledge = captureDraftSave(form);
      const result = await api.channelTest(entry.id, input.value, execute);
      acknowledge();
      if (!root.isConnected) return;
      showJSON(form, result, "查看测试结果 / 模型任务");
      notify(
        execute
          ? "模型任务已显式启动，可在模型与查询中核对结果和费用。"
          : "原文查询完成，没有模型或手机发送。",
      );
    });
  }
  queryDetails.append(form);
  root.append(queryDetails);
  const records = el("details"),
    eventsPanel = el("div");
  records.append(
    el("summary", uiText("消息执行与发送记录")),
    control("刷新执行记录", async () => {
      const events = await api.channelEvents(entry.id);
      if (!root.isConnected) return;
      const nodes = events.map((event) => {
        const line = el("article", null, "record-card");
        line.append(el("p", `${event.event_id} · ${event.state}`));
        if (event.delivery_parts?.length) {
          const details = el("details");
          details.append(el("summary", uiText("分项发送结果")));
          for (const part of event.delivery_parts)
            details.append(
              el(
                "p",
                `${part.part} · ${part.kind} · ${part.state}${part.reason ? " · " + part.reason : ""}`,
              ),
            );
          line.append(details);
        }
        if (["unknown", "sending"].includes(event.state)) {
          line.append(
            el(
              "p",
              uiText("请先暂停入口，并在手机核对是否收到。核对不会重发。"),
            ),
          );
          for (const [text, acknowledgement] of [
            ["手机已收到", "sent"],
            ["手机确认未收到", "not_sent"],
          ])
            line.append(
              control(text, async () => {
                await api.channelReconcile(
                  entry.id,
                  event.event_id,
                  acknowledgement,
                );
                line.append(el("p", uiText("核对已记录，不会自动重发。")));
                await refresh();
              }),
            );
        }
        return line;
      });
      eventsPanel.replaceChildren(
        ...(nodes.length ? nodes : [el("p", uiText("暂无执行记录。"))]),
      );
    }),
    eventsPanel,
  );
  root.append(records);
  let pairingStamp = "";
  return {
    root,
    dispose() {
      intake.stop();
    },
    update(current, stale = false) {
      entry = current;
      relation.replaceChildren(relationCard(entry));
      title.textContent = entry.name;
      setText(
        meta,
        phrase("{p0} · {p1} · {p2} · 配置版本 {p3}", {
          p0: entry.platform === "feishu" ? uiText("飞书") : uiText("微信"),
          p1:
            entry.origin === "legacy"
              ? uiText("本项目既有 gateway")
              : uiText("GUI 创建的独立入口"),
          p2: uiText(channelLabel(entry.state)),
          p3: entry.revision,
        }),
      );
      status.update(entry, stale);
      services.update(entry, stale);
      setText(
        intakeSummary,
        stale
          ? uiText("自动入库状态已过期，请刷新。")
          : entry.intake
            ? phrase("自动入库{p0} · 目标库：{p1}{p2}", {
                p0: entry.intake.enabled ? uiText("已开启") : uiText("已关闭"),
                p1: entry.intake.target_name || "不可用",
                p2: entry.intake.reason
                  ? " · " + intakeLabel(entry.intake.reason)
                  : "",
              })
            : uiText("自动入库策略尚未配置，默认关闭。"),
      );
      management.update(entry);
      if (!stale) intake.observe();
      queryDetails.hidden =
        entry.origin !== "gui" || entry.services?.purpose === "intake";
      records.hidden = entry.origin !== "gui";
      const blocked = Boolean(
        entry.operation &&
        ["pending", "needs_review"].includes(entry.operation.state),
      );
      model.dataset.policyDisabled = String(
        blocked || entry.configuration?.settings.answer_mode === "evidence",
      );
      preview.dataset.policyDisabled = String(blocked);
      if (model.getAttribute("aria-busy") !== "true")
        model.disabled = model.dataset.policyDisabled === "true";
      if (preview.getAttribute("aria-busy") !== "true")
        preview.disabled = blocked;
      setText(
        diagnosticBody,
        uiText(
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
      const nextPairing = JSON.stringify([
        entry.state,
        entry.configuration?.challenge,
      ]);
      if (pairingStamp !== nextPairing) {
        pairingStamp = nextPairing;
        pairing.replaceChildren();
        if (entry.configuration?.challenge)
          pairing.append(
            el(
              "p",
              uiText("请用本人账号私聊机器人发送以下完整文字，再在本机确认："),
            ),
            el("pre", entry.configuration.challenge),
          );
        if (entry.state === "login")
          pairing.append(
            control("显示 / 刷新真实登录二维码", async () => {
              const source = await api.channelQr(entry.id);
              if (!root.isConnected || entry.state !== "login") return;
              let image = pairing.querySelector("img");
              if (!image) {
                image = el("img");
                image.alt = "微信本人登录二维码";
                image.width = 240;
                pairing.append(image);
              }
              image.src = source;
            }),
          );
      }
    },
  };
}
