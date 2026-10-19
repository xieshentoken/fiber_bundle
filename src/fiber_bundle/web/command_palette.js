import { languageAliases, setText, uiText } from "./i18n/index.js";
// Local page/action navigation. The existing delegated handler owns draft checks.
import { el } from "./ui.js";
import { icon } from "./icons.js";
export function matches(item, query) {
  return `${languageAliases(item.label)} ${item.keywords || ""}`
    .toLowerCase()
    .includes(query.trim().toLowerCase());
}
const actions = [
  {
    label: "查询知识库内容",
    page: "models",
    keywords: "搜索 笔记 全文 search query",
  },
  {
    label: "普通资料导入",
    page: "imports",
    engine: "ordinary",
    keywords: "import 文件 上传",
  },
  {
    label: "fake-expert 专用导入",
    page: "imports",
    engine: "fake-expert",
    keywords: "专家 pdf",
  },
  { label: "打开收件箱", page: "worklist", keywords: "inbox 待办 审核" },
];
export function createCommandPalette(signedIn) {
  const trigger = document.getElementById("command-open"),
    dialog = el("dialog", null, "command-palette"),
    input = el("input"),
    list = el("div", null, "command-list"),
    status = el("p", null, "command-status");
  dialog.id = "command-palette";
  setText(dialog, uiText("搜索页面与操作"), "aria-label");
  input.type = "search";
  setText(input, uiText("输入页面或操作名称…"), "placeholder");
  input.maxLength = 200;
  setText(input, uiText("搜索页面与操作"), "aria-label");
  input.dataset.draft = "ignore";
  dialog.append(input, list, status);
  document.body.append(dialog);
  let origin = null;
  function close() {
    if (dialog.open) dialog.close();
  }
  function render() {
    const pages = [...document.querySelectorAll("[data-tab]")].map((b) => ({
      label: b.dataset.label,
      page: b.dataset.tab,
      keywords: b.dataset.tab,
    }));
    const items = [...actions, ...pages].filter((item) =>
      matches(item, input.value),
    );
    list.replaceChildren(
      ...items.map((item) => {
        const button = el("button");
        button.type = "button";
        button.append(icon(item.page), el("span", uiText(item.label)));
        button.addEventListener("click", () => {
          close();
          const proxy = el("button");
          proxy.hidden = true;
          proxy.type = "button";
          proxy.dataset.pageTarget = item.page;
          if (item.engine) proxy.dataset.importEngine = item.engine;
          document.body.append(proxy);
          proxy.click();
          proxy.remove();
        });
        return button;
      }),
    );
    setText(
      status,
      items.length
        ? uiText("只搜索页面与操作；笔记检索使用现有查询入口。")
        : uiText("没有匹配的页面或操作。"),
    );
  }
  function open() {
    if (!signedIn() || document.querySelector("dialog[open]")) return;
    origin = document.activeElement;
    input.value = "";
    render();
    dialog.showModal();
    input.focus();
  }
  function shortcut(event) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      dialog.open ? close() : open();
    }
  }
  input.addEventListener("input", render);
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    const buttons = [...list.querySelectorAll("button")],
      i = buttons.indexOf(document.activeElement);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      buttons[(i + 1) % buttons.length]?.focus();
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (i <= 0) input.focus();
      else buttons[i - 1].focus();
    }
    if (event.key === "Enter" && document.activeElement === input) {
      event.preventDefault();
      buttons[0]?.click();
    }
  });
  dialog.addEventListener("close", () => {
    input.value = "";
    list.replaceChildren();
    if (origin?.isConnected) origin.focus({ preventScroll: true });
  });
  trigger.addEventListener("click", open);
  document.addEventListener("keydown", shortcut);
  return {
    close,
    destroy() {
      close();
      trigger.removeEventListener("click", open);
      document.removeEventListener("keydown", shortcut);
      dialog.remove();
    },
  };
}
