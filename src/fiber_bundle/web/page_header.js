import { localizedDate, phrase, setText, uiText } from "./i18n/index.js";
// Shared page header; existing views retain their request/action owners.
import { $, el } from "./ui.js";
import { icon } from "./icons.js";
const descriptions = {
  vaults: ["保存与整理", "查看知识库状态，管理保存规则、索引和外部改动。"],
  imports: [
    "从原件到候选",
    "选择资料、目标知识库和提取方式，提交后查看进度与保存结果。",
  ],
  worklist: [
    "集中处理，保留原始证据",
    "待我处理、处理中与历史分开查看，进入记录继续操作。",
  ],
  pending: ["知识库接入审批", "核对申请方、目标库与授予权限，再批准启用。"],
  batches: [
    "选择批次处理方式",
    "收件缓冲与等待本人选择分开显示。冻结后选择处理方式；未知结果先核对，不重发。",
  ],
  documents: [
    "文档与文章审核",
    "核对完整来源及候选中的内容、图片和数字，再记录实际判断。执行完成不代表已经发布。",
  ],
  media: [
    "音频与画面审核",
    "核对完整音视频、转录覆盖和术语疑点。抽样帧不能替代完整画面审核；保留人工审核接口。",
  ],
  channels: ["收件、绑定与连接", "查看飞书与微信的连接、自动保存和问答状态。"],
  clients: [
    "Agent 接入与客户端权限",
    "管理 Agent 接入、查询范围和客户端权限。",
  ],
  models: [
    "配置路由，按需验证",
    "保存模型与预算后，明确发起测试。配置存在或历史调用成功不代表当前可用；首页不会自动调用模型。",
  ],
  execution: [
    "执行记录与费用口径",
    "查看任务进度与异常，或切换到用量与费用。云费用按配置单价估算。",
  ],
  inventory: [
    "原件、版本与处理配置",
    "按身份和知识库查看原件、来源版本及处理设置。",
  ],
  graph: [
    "笔记关联与 MOC",
    "整理笔记、管理 MOC，或进入关联分析与自动归类设置。",
  ],
  creation: ["有来源的文档制作", "准备制作要求，跟踪草稿、确认和保存结果。"],
  backend: ["后台运行与提醒", "查看后台状态、提醒设置和组件版本。"],
  backups: ["备份、恢复与分发", "准备备份或恢复，按当前阶段完成执行与核验。"],
  settings: ["本机显示与会话", "调整外观与阅读偏好，或配置登录会话。"],
};
const copy = Object.fromEntries(
  Object.entries(descriptions).map(([page, [, summary]]) => [
    page,
    { summary, notes: [] },
  ]),
);
copy.home = { summary: "", notes: [] };
const parents = {
  pending: "worklist",
  batches: "worklist",
  documents: "worklist",
  media: "worklist",
};
export function pageCopy(page) {
  return copy[page] || null;
}
export function pageParent(page) {
  return parents[page] || null;
}
function clock(date) {
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}
// Home greets by the local device time; other pages keep their navigation label.
export function greeting(date = new Date()) {
  const hour = date.getHours();
  if (hour >= 5 && hour < 11) return "早上好";
  if (hour >= 11 && hour < 13) return "中午好";
  if (hour >= 13 && hour < 18) return "下午好";
  return "晚上好";
}
export function dateText(date = new Date()) {
  const weekday = ["日", "一", "二", "三", "四", "五", "六"][date.getDay()];
  return `${date.getMonth() + 1}月${date.getDate()}日 星期${weekday}`;
}
function withIcon(button) {
  if (!button.querySelector(".icon")) button.prepend(icon("refresh"));
}
// Page-specific refresh buttons move into the shared header while their page is shown.
export function createPageHeader() {
  const actions = $("page-actions"),
    generic = $("page-refresh"),
    updated = $("page-updated");
  withIcon(generic);
  let moved = null,
    placeholder = null,
    shown = "";
  function restore() {
    if (moved && placeholder?.isConnected) placeholder.replaceWith(moved);
    moved = null;
    placeholder = null;
  }
  actions.addEventListener("fiber:action-done", (event) => {
    if (
      event.target.closest?.("[data-page-refresh]") &&
      !event.detail?.declined
    )
      stamp();
  });
  // Home and worklist cards carry their own read times.
  function stamp(date = new Date()) {
    if (!shown || ["home", "worklist"].includes(shown)) return;
    setText(updated, phrase("更新于 {p0}", { p0: clock(date) }));
    updated.hidden = false;
  }
  return {
    show(page) {
      restore();
      shown = page;
      const info = copy[page],
        parent = parents[page];
      const home = page === "home";
      if (home) setText($("page-name"), uiText(greeting()));
      setText(
        $("page-date"),
        home
          ? localizedDate(new Date(), {
              month: "long",
              day: "numeric",
              weekday: "long",
            })
          : "",
      );
      $("page-date").hidden = !home;
      for (const id of ["page-extra", "page-extra-end"]) {
        $(id).hidden = !home;
        if (!home) $(id).replaceChildren();
      }
      setText($("page-summary"), uiText(info?.summary || ""));
      $("page-summary").hidden = !info?.summary;
      $("page-notes-list").replaceChildren(
        ...(info?.notes || []).map((note) => el("li", note)),
      );
      $("page-notes").hidden = !info?.notes?.length;
      $("page-notes").open = false;
      const crumb = $("page-breadcrumb");
      crumb.hidden = !parent;
      if (parent) {
        const button = crumb.querySelector("button");
        button.dataset.pageTarget = parent;
        setText(
          button,
          uiText(
            document.querySelector(`[data-tab="${parent}"]`)?.dataset.label ||
              parent,
          ),
        );
      }
      updated.hidden = true;
      setText(updated, uiText(""));
      const own = $(page + "-panel")?.querySelector("[data-page-refresh]");
      if (own) {
        placeholder = document.createComment("page-refresh");
        own.before(placeholder);
        withIcon(own);
        actions.insertBefore(own, generic);
        moved = own;
      }
      generic.hidden = Boolean(own) || page === "settings";
      generic.toggleAttribute("data-home-refresh", page === "home");
    },
    stamp,
    clear() {
      restore();
      shown = "";
      updated.hidden = true;
      setText(updated, uiText(""));
    },
  };
}
