import { setText, uiText, phrase } from "./i18n/index.js";
import { channelIssuesText, showChannelIssues } from "./channel_validation.js";
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { confirmAction } from "./confirm_dialog.js";

export function createChannelActions(api, getEntry, refresh, edit) {
  const root = el("div", null, "panel-actions");
  const buttons = new Map();
  const readiness = el("div", null, "error");
  readiness.hidden = true;
  readiness.id = "channel-start-" + getEntry().id;
  root.append(readiness);
  function add(key, label, work) {
    const button = control(label, work);
    button.dataset.channelAction = key;
    buttons.set(key, button);
    root.append(button);
  }
  async function command(action) {
    const entry = getEntry();
    if (action === "start" && entry.origin !== "legacy") {
      let check;
      try {
        check = await api.channelReadiness(entry.id);
      } catch (error) {
        if (error.issues?.length) showChannelIssues(readiness, error.issues);
        else {
          setText(readiness, uiText(error.message));
          readiness.hidden = false;
        }
        throw error;
      }
      showChannelIssues(readiness, check.issues);
      if (!check.ready)
        throw new Error("无法启动：" + channelIssuesText(check.issues));
    }
    if (entry.origin === "legacy") {
      await api.legacyChannelStart(entry.id, {
        expected_revision: entry.revision,
        configuration_sha256: entry.configuration_sha256,
      });
    } else await api.channelAction(entry.id, action);
    await refresh();
  }
  async function lifecycle(action, notify) {
    const entry = getEntry();
    const preview = await api.channelLifecyclePreview(entry.id, action);
    if (!root.isConnected || root.closest("[hidden]")) return;
    if (
      action !== "pause" &&
      !(await confirmAction({
        title: action === "remove" ? "移除这个消息入口？" : "解除本人绑定？",
        body: preview.confirmation,
        confirmLabel: action === "remove" ? "移除连接" : "解除绑定",
        cancelLabel: "保留入口",
        tone: "danger",
      }))
    )
      return;
    await api.channelLifecycle(entry.id, {
      action,
      operation_id: window.crypto.randomUUID(),
      expected_revision: preview.revision,
      configuration_sha256: preview.configuration_sha256,
      expected_instance_id: preview.instance_id,
    });
    await refresh();
    notify("操作已记录。请查看停止与清理进度，笔记和执行审计会保留。");
  }
  add("edit", "编辑配置", async () => edit(getEntry().configuration));
  add("check", "验证连接", async () => command("check"));
  add("bind", "开始本人配对", async () =>
    command(getEntry().platform === "feishu" ? "pair" : "login"),
  );
  add("confirm", "确认本人绑定", async () => {
    const entry = getEntry(),
      candidate = entry.configuration.candidate_owner;
    await api.channelConfirmOwner(entry.id, entry.revision, candidate);
    await refresh();
  });
  add("start", "启动 / 重新连接", async () => command("start"));
  add("pause", "暂停接收", async (notify) => lifecycle("pause", notify));
  add("unbind", "解除绑定", async (notify) => lifecycle("unbind", notify));
  add("remove", "移除连接", async (notify) => lifecycle("remove", notify));
  add("resume", "继续核对操作", async () => {
    const entry = getEntry();
    await api.channelLifecycleResume(entry.id, entry.operation.id);
    await refresh();
  });
  return {
    root,
    update(entry) {
      const gui = entry.origin === "gui",
        pending =
          entry.operation &&
          ["pending", "needs_review"].includes(entry.operation.state),
        stopped = entry.runtime.process_state === "stopped",
        candidate = entry.configuration?.candidate_owner;
      for (const [key, button] of buttons) {
        button.hidden =
          ["edit", "check", "bind", "confirm"].includes(key) && !gui;
        if (key === "confirm") button.hidden = !candidate;
        if (key === "resume")
          button.hidden = !pending || entry.operation.state !== "needs_review";
        const disabled =
          key === "resume"
            ? false
            : pending ||
              (["edit", "check", "bind", "confirm", "start"].includes(key) &&
                !stopped) ||
              (key === "start" && (!entry.owner_id || !entry.start_supported));
        button.dataset.policyDisabled = String(Boolean(disabled));
        if (button.getAttribute("aria-busy") !== "true")
          button.disabled = Boolean(disabled);
      }
      setText(
        buttons.get("bind"),
        entry.platform === "feishu"
          ? uiText("开始本人配对")
          : uiText("扫码登录 / 重新登录"),
      );
      setText(
        buttons.get("confirm"),
        candidate
          ? phrase("确认本人：{p0}", { p0: candidate })
          : uiText("确认本人绑定"),
      );
    },
  };
}
