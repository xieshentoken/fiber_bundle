import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { phrase, setText, uiText } from "./i18n/index.js";

export function channelPurpose(entry) {
  if (entry.services?.purpose) return entry.services.purpose;
  return entry.configuration?.settings?.add ? "both" : "query";
}
const names = { query: "知识库查询", intake: "笔记保存" };
const states = {
  legacy: "尚未启用独立运行",
  idle: "运行中",
  working: "正在处理",
  starting: "启动中",
  draining: "正在结束当前阶段",
  waiting_storage: "等待存储",
  blocked: "处理需核对",
};
export function createChannelServices(api, getEntry, refresh) {
  const root = el("section", null, "channel-services"),
    heading = el("h4", uiText("独立运行")),
    hint = el(
      "p",
      uiText("查询、接收与保存分别控制。暂停处理不会断开机器人连接。"),
      "muted",
    );
  const enable = control("启用独立运行（保留历史任务）", async () => {
    const entry = getEntry();
    await api.channelServicesActivate(entry.id, entry.revision);
    await refresh();
  });
  root.append(heading, hint, enable);
  const grid = el("div", null, "channel-service-grid");
  root.append(grid);
  const rows = new Map();
  function row(role) {
    const section = el("article", null, "record-card"),
      title = el("h4", uiText(names[role])),
      status = el("p"),
      history = el("p", null, "muted");
    section.dataset.serviceRole = role;
    const run = (operation) => async () => {
      const entry = getEntry(),
        service = entry.services.roles[role];
      await api.channelServiceAction(
        entry.id,
        role,
        operation,
        service.revision,
      );
      await refresh();
    };
    const start = control(
        role === "query" ? "继续查询" : "继续保存",
        run("start"),
      ),
      pause = control(role === "query" ? "暂停查询" : "暂停保存", run("pause")),
      update = control(
        role === "query" ? "更新查询服务" : "更新保存服务",
        run("update"),
      );
    const actions = el("div", null, "panel-actions");
    actions.append(start, pause, update);
    const detail = el("details"),
      diagnostic = el("p");
    detail.append(el("summary", uiText("服务诊断")), diagnostic);
    section.append(title, status, history, actions, detail);
    grid.append(section);
    return { section, status, history, start, pause, update, diagnostic };
  }
  return {
    root,
    update(entry, stale = false) {
      const services = entry.services;
      root.hidden = !services?.supported;
      if (root.hidden) return;
      const managed = Object.values(services.roles).some((s) => s.managed);
      enable.hidden = managed;
      const enableDisabled =
        stale ||
        !entry.owner_id ||
        !["running", "paused"].includes(entry.state);
      enable.dataset.policyDisabled = String(enableDisabled);
      if (enable.getAttribute("aria-busy") !== "true")
        enable.disabled = enableDisabled;
      for (const [role, service] of Object.entries(services.roles)) {
        if (!rows.has(role)) rows.set(role, row(role));
        const view = rows.get(role);
        view.section.hidden = false;
        const label =
          service.state === "stopped"
            ? service.enabled
              ? "服务未运行"
              : "已暂停"
            : states[service.state] || "状态待核对";
        setText(view.status, uiText(label));
        view.history.hidden = !service.held_history;
        setText(
          view.history,
          phrase("历史未完成记录 {p0} 项，未自动恢复。", {
            p0: service.held_history || 0,
          }),
        );
        view.start.hidden = !managed || service.process_running;
        view.pause.hidden = !managed || !service.enabled;
        view.update.hidden = !managed;
        for (const button of [view.start, view.pause, view.update]) {
          const disabled =
            stale ||
            !entry.owner_id ||
            (button !== view.pause && Boolean(service.process_running)) ||
            (button === view.update && service.enabled);
          button.dataset.policyDisabled = String(disabled);
          if (button.getAttribute("aria-busy") !== "true")
            button.disabled = disabled;
        }
        setText(
          view.diagnostic,
          phrase("运行版本：{p0}；状态：{p1}", {
            p0: service.runtime_version?.slice(0, 12) || "—",
            p1: service.error || uiText(label),
          }),
        );
      }
      for (const [role, view] of rows)
        if (!services.roles[role]) view.section.hidden = true;
    },
  };
}
