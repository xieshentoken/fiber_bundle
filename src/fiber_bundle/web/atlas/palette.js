// ⌘K palette: jump to pages and actions, or hand a query to the Ask page.
// Navigation goes through ctx.navigate, so the unsaved-draft guard still applies.
import { h, icon, clear } from "./dom.js";
import { strings } from "./i18n.js";
import { groups, icons, navText, pageTitle } from "./nav.js";

const t = strings({
  zh: {
    label: "搜索或跳转",
    placeholder: "输入页面、操作或要检索的关键词…",
    pages: "页面",
    actions: "操作",
    search: "在笔记中检索“{q}”",
    searchHint: "以有查询权限的身份检索默认库，只返回片段",
    none: "没有匹配的页面或操作。",
    importOrdinary: "普通资料导入",
    importExpert: "fake-expert 专用导入",
    reviewInbox: "处理收件箱",
    newClient: "新建客户端身份",
    connectVault: "接入笔记仓库",
    hint: "↑↓ 选择 · Enter 打开 · Esc 关闭",
  },
  en: {
    label: "Search or jump",
    placeholder: "Type a page, an action, or keywords to search…",
    pages: "Pages",
    actions: "Actions",
    search: "Search notes for “{q}”",
    searchHint:
      "Searches default vaults as an identity with query access; snippets only",
    none: "No matching page or action.",
    importOrdinary: "Regular import",
    importExpert: "fake-expert import",
    reviewInbox: "Work through the inbox",
    newClient: "New client identity",
    connectVault: "Connect a note vault",
    hint: "↑↓ select · Enter open · Esc close",
  },
});
const actions = () => [
  {
    label: t("importOrdinary"),
    keywords: "import upload file 导入 上传",
    page: "imports",
    params: { engine: "ordinary" },
    iconName: "imports",
  },
  {
    label: t("importExpert"),
    keywords: "expert pdf 专家",
    page: "imports",
    params: { engine: "fake-expert" },
    iconName: "expert",
  },
  {
    label: t("reviewInbox"),
    keywords: "inbox review 待办 审核",
    page: "worklist",
    iconName: "inbox",
  },
  {
    label: t("connectVault"),
    keywords: "vault connect obsidian 仓库 接入",
    page: "vaults",
    sub: "connect",
    iconName: "vaults",
  },
  {
    label: t("newClient"),
    keywords: "client token agent 身份 凭证",
    page: "clients",
    sub: "clients",
    iconName: "key",
  },
];
export function matches(item, query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [item.label, item.keywords, item.group]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .includes(needle);
}
export function createPalette(ctx) {
  let enabled = false,
    items = [],
    active = 0;
  const input = h("input.a-palette-input", {
    type: "search",
    placeholder: t("placeholder"),
    maxLength: 200,
    autocomplete: "off",
    role: "combobox",
    "aria-expanded": "true",
    "aria-controls": "a-palette-list",
    "aria-autocomplete": "list",
    "aria-label": t("label"),
  });
  const list = h("ul.a-palette-list", {
    id: "a-palette-list",
    role: "listbox",
  });
  const dialog = h(
    "dialog.a-palette",
    { "aria-label": t("label") },
    h("div.a-palette-field", icon("search"), input, h("kbd", "Esc")),
    list,
    h("p.a-palette-hint", t("hint")),
  );
  document.body.append(dialog);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  function entries(query) {
    const pages = groups.flatMap((group) =>
      group.pages.map((page) => ({
        kind: "page",
        label: pageTitle(page),
        group: group.id ? navText("group_" + group.id) : t("pages"),
        keywords: page,
        page,
        iconName: icons[page],
      })),
    );
    const found = [
      ...actions().map((a) => ({ ...a, kind: "action", group: t("actions") })),
      ...pages,
    ].filter((item) => matches(item, query));
    if (query.trim().length >= 2)
      found.push({
        kind: "search",
        label: t("search", { q: query.trim() }),
        group: t("searchHint"),
        page: "ask",
        params: { q: query.trim() },
        iconName: "search",
      });
    return found;
  }
  function render() {
    items = entries(input.value);
    active = Math.min(active, Math.max(items.length - 1, 0));
    clear(
      list,
      items.length
        ? items.map((item, index) => {
            const node = h(
              "li.a-palette-option",
              {
                id: "a-opt-" + index,
                role: "option",
                "aria-selected": String(index === active),
              },
              icon(item.iconName),
              h("span.a-palette-text", item.label),
              h("span.a-palette-group", item.group),
            );
            node.addEventListener(
              "pointermove",
              () => index !== active && select(index),
            );
            node.addEventListener("click", () => activate(item));
            return node;
          })
        : h("li.a-palette-empty", t("none")),
    );
    input.setAttribute(
      "aria-activedescendant",
      items.length ? "a-opt-" + active : "",
    );
  }
  function select(index) {
    active = index;
    [...list.children].forEach((node, position) =>
      node.setAttribute("aria-selected", String(position === index)),
    );
    list.children[index]?.scrollIntoView({ block: "nearest" });
    input.setAttribute("aria-activedescendant", "a-opt-" + index);
  }
  function activate(item) {
    if (!item) return;
    dialog.close();
    ctx.navigate(item.page, item.sub || "", item.params || {});
  }
  input.addEventListener("input", () => {
    active = 0;
    render();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!items.length) return;
      select(
        (active + (event.key === "ArrowDown" ? 1 : -1) + items.length) %
          items.length,
      );
    } else if (event.key === "Enter") {
      event.preventDefault();
      activate(items[active]);
    }
  });
  return {
    open() {
      if (!enabled) return;
      if (dialog.open) {
        dialog.close();
        return;
      }
      input.value = "";
      active = 0;
      render();
      dialog.showModal();
      input.focus();
    },
    close() {
      if (dialog.open) dialog.close();
    },
    setEnabled(value) {
      enabled = value;
    },
  };
}
