import { phrase, joinText, textNode, setText, uiText } from "./i18n/index.js";
// 收件箱: every current to-do from the home reads, filterable by source.
// What counts as a to-do stays defined by home_status.pendingItems.
import { el } from "./ui.js";
import { activityRows, activityCoverage } from "./home_activity.js";
import { control } from "./record_ui.js";
import { todoGroups, todoRows } from "./home_model.js";
import { readLabels } from "./home_cards.js";
import { row, stampText } from "./ui_cards.js";

const PER_SOURCE = 25;
let view = "pending",
  query = "",
  page = 0,
  scopeKey = "";
let filter = null;
const reviewPages = {
  pending: ["vault"],
  batches: ["batch"],
  documents: ["documents", "articles"],
  media: ["media", "videos"],
};
export function inboxSections(rows) {
  const counts = {};
  for (const [page, sources] of Object.entries(reviewPages))
    counts[page] = rows.filter((r) => sources.includes(r.source)).length;
  return counts;
}
export function renderInbox(root, records, scopeText, rerender) {
  if (scopeKey !== scopeText) {
    scopeKey = scopeText;
    filter = null;
    query = "";
    page = 0;
  }
  const pending = todoRows(records),
    activity = activityRows(records);
  const coverage = activityCoverage(records);
  const rows = view === "pending" ? pending : activity[view];
  const groups = todoGroups(rows);
  if (filter && !groups.some((g) => g.source === filter)) filter = null;
  // Static review-page links live in index.html; add their current counts.
  const counts = inboxSections(pending);
  for (const [page, count] of Object.entries(counts)) {
    const link = document.querySelector(
      `#worklist-panel .inbox-pages [data-page-target="${page}"]`,
    );
    if (!link) continue;
    link.querySelector(".inbox-count")?.remove();
    if (count) link.append(el("b", String(count), "inbox-count"));
  }
  const filters = el("div", null, "inbox-filters");
  filters.setAttribute("role", "group");
  setText(filters, uiText("按来源筛选"), "aria-label");
  for (const group of [
    { source: null, label: "全部", count: rows.length },
    ...groups,
  ]) {
    const chip = el("button", null, "ui-chip");
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(filter === group.source));
    chip.append(
      textNode(joinText([uiText(group.label), " "])),
      el("b", String(group.count)),
    );
    chip.addEventListener("click", () => {
      filter = group.source;
      rerender();
    });
    filters.append(chip);
  }
  const list = el("div", null, "ui-rows inbox-rows");
  const shown = rows.filter(
    (r) =>
      (!filter || r.source === filter) &&
      (!query ||
        `${r.title} ${r.detail}`
          .toLocaleLowerCase()
          .includes(query.toLocaleLowerCase())),
  );
  page = Math.min(page, Math.max(0, Math.ceil(shown.length / PER_SOURCE) - 1));
  for (const item of shown.slice(page * PER_SOURCE, (page + 1) * PER_SOURCE))
    list.append(row(item, item.detail));
  const paging = el("div", null, "task-pages");
  if (page)
    paging.append(
      control("上一页", async () => {
        page--;
        rerender();
      }),
    );
  paging.append(
    el(
      "span",
      phrase("当前已读 {p0} 项 · 第 {p1} / {p2} 页", {
        p0: shown.length,
        p1: page + 1,
        p2: Math.max(1, Math.ceil(shown.length / PER_SOURCE)),
      }),
    ),
  );
  if ((page + 1) * PER_SOURCE < shown.length)
    paging.append(
      control("下一页", async () => {
        page++;
        rerender();
      }),
    );
  if (!rows.length)
    list.append(
      el(
        "p",
        view === "pending"
          ? uiText("已读取范围内暂无需要你处理的事项。")
          : view === "processing"
            ? uiText("已读取范围内暂无正在处理的任务。")
            : uiText("已读取范围内暂无历史结果。"),
        "ui-empty",
      ),
    );
  const details = el("details", null, "ui-details");
  details.append(
    el("summary", uiText("读取范围与缺口")),
    el(
      "p",
      uiText(
        "文档、媒体、文章及所选范围导入：最近50条；批次、执行及专家指令：最多100条。跨来源可能重复，不作为全库总量。",
      ),
      "muted",
    ),
  );
  for (const name of [
    "vaults",
    "batches",
    "documents",
    "media",
    "videos",
    "articles",
    "imports",
    "expert",
    "automatic",
  ]) {
    const scoped = ["imports", "expert", "automatic", "videos"].includes(name);
    details.append(
      el(
        "p",
        joinText(
          [
            uiText(readLabels[name]),
            scoped
              ? scopeText || uiText("尚未选择身份 / 库")
              : uiText("管理员可见范围"),
            uiText(stampText(records[name])),
          ],
          " · ",
        ),
        records[name]?.error ? "error" : "muted",
      ),
    );
  }
  const card = el("section", null, "ui-card inbox-list");
  setText(card, uiText("待办列表"), "aria-label");
  const tabs = el("div", null, "view-tabs");
  tabs.setAttribute("role", "group");
  setText(tabs, uiText("收件箱状态"), "aria-label");
  for (const [key, label, count] of [
    ["pending", "待我处理", pending.length],
    ["processing", "处理中", activity.processing.length],
    ["history", "历史", activity.history.length],
  ]) {
    const button = control(
      joinText([uiText(label), count || (coverage ? 0 : "—")], " "),
      async () => {
        view = key;
        filter = null;
        page = 0;
        rerender();
      },
    );
    button.setAttribute("aria-pressed", String(view === key));
    tabs.append(button);
  }
  const search = el("input");
  setText(search, uiText("查找收件记录"), "aria-label");
  setText(search, uiText("按资料标题查找"), "placeholder");
  search.value = query;
  const searchForm = el("form", null, "task-filters");
  searchForm.append(
    search,
    control("查找", async () => {
      query = search.value;
      page = 0;
      rerender();
    }),
  );
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    query = search.value;
    page = 0;
    rerender();
  });
  card.append(tabs);
  if (!coverage)
    card.append(
      el(
        "p",
        uiText(
          "部分来源尚未读取或刷新失败；数量仅含已读记录，未确认的零项显示为 —。",
        ),
        "task-impact",
      ),
    );
  card.append(filters, searchForm, list, paging, details);
  root.replaceChildren(card);
  return pending.length;
}
