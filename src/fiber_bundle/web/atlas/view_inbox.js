// Inbox: every current to-do with review sections. Each item is handled one at a time;
// nothing is approved or published in bulk.
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import { pendingItems } from "../home_status.js";
import { createHomeLoader } from "./home_loader.js";
import { todoGroups, todoRows, readText, t as homeText } from "./home_model.js";
import {
  chips,
  disclosure,
  empty,
  listRow,
  skeleton,
  tabs,
  card,
} from "./ui.js";

const t = strings({
  zh: {
    all: "全部待办",
    pending: "空库审批",
    batches: "消息批次",
    documents: "文档与文章",
    media: "媒体",
    sections: "收件箱分区",
    filter: "按来源筛选",
    everything: "全部",
    none: "已读取范围内暂无明确待处理事项；尚未读取或失败的来源见“读取范围与缺口”。",
    scope: "读取范围与缺口",
    scopeNote:
      "文档、媒体、文章及所选范围导入：最近 50 条；批次、执行及专家指令：最多 100 条。跨来源可能重复，不作为全库总量。",
    admin: "管理员可见范围",
    noScope: "尚未选择身份 / 库",
    selected: "默认导入范围",
    stale: "上次读取 · 需刷新",
  },
  en: {
    all: "All to-dos",
    pending: "Vault approvals",
    batches: "Message batches",
    documents: "Documents & articles",
    media: "Media",
    sections: "Inbox sections",
    filter: "Filter by source",
    everything: "All",
    none: "Nothing clearly waiting within what was read. Sources that are unread or failed are listed under “Read scope and gaps”.",
    scope: "Read scope and gaps",
    scopeNote:
      "Documents, media, articles and scoped imports: latest 50; batches, runs and expert commands: up to 100. Sources may overlap; these are not vault totals.",
    admin: "Admin-visible scope",
    noScope: "No identity / vault selected",
    selected: "Default import scope",
    stale: "Last read · needs refresh",
  },
});
const sectionSources = {
  pending: ["vault"],
  batches: ["batch"],
  documents: ["documents", "articles"],
  media: ["media"],
};
const loaders = {
  pending: () => import("./inbox_pending.js"),
  batches: () => import("./inbox_batches.js"),
  documents: () => import("./inbox_documents.js"),
  media: () => import("./inbox_media.js"),
};

export function createView(ctx, route) {
  let sub = route.sub || "",
    params = route.params || {},
    filter = null,
    section = null,
    frame = 0,
    generation = 0;
  const tabsHost = h("div.a-inbox-tabs");
  const body = h("div.a-inbox-body");
  const root = h("div.a-stack.a-inbox", tabsHost, body);
  const loader = createHomeLoader(ctx.api, ctx.session, () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(drawFrame);
  });
  function counts() {
    const rows = todoRows(loader.records);
    const result = { "": rows.length };
    for (const [name, sources] of Object.entries(sectionSources))
      result[name] = rows.filter((row) => sources.includes(row.source)).length;
    return result;
  }
  function drawFrame() {
    const known = Object.keys(loader.records).length > 0;
    ctx.setBadge(
      "worklist",
      known ? pendingItems(loader.records).length : null,
    );
    const n = counts();
    clear(
      tabsHost,
      tabs(
        ["", "pending", "batches", "documents", "media"].map((value) => ({
          value,
          label: t(value || "all"),
          count: known ? n[value] : null,
        })),
        sub,
        (value) => ctx.navigate("worklist", value),
        t("sections"),
      ),
    );
    if (!sub) drawAll();
  }
  function drawAll() {
    const records = loader.records;
    const rows = todoRows(records);
    const groups = todoGroups(rows);
    if (filter && !groups.some((g) => g.source === filter)) filter = null;
    const shown = rows.filter((row) => !filter || row.source === filter);
    const list = h("div.a-rows");
    const perSource = new Map();
    for (const row of shown) {
      const used = perSource.get(row.source) || 0;
      if (used >= 50) continue;
      perSource.set(row.source, used + 1);
      list.append(
        listRow({
          iconName: {
            batch: "batch",
            documents: "document",
            media: "media",
            articles: "article",
            vault: "approval",
            imports: "imports",
            expert: "expert",
            automatic: "automatic",
            channel: "channels",
          }[row.source],
          title: row.title,
          detail: [row.sourceLabel, row.stale ? t("stale") : null, row.detail]
            .filter(Boolean)
            .join(" · "),
          status: row.status,
          onClick: () => openRow(row),
        }),
      );
    }
    const gaps = h("div.a-stack", h("p.a-subtle", t("scopeNote")));
    const sourceKey = { vaults: "vault", batches: "batch" };
    for (const name of [
      "vaults",
      "batches",
      "documents",
      "media",
      "articles",
      "imports",
      "expert",
      "automatic",
    ]) {
      const scoped = ["imports", "expert", "automatic"].includes(name);
      const scopeText = scoped
        ? loader.scope.client_id
          ? t("selected")
          : t("noScope")
        : t("admin");
      gaps.append(
        h(
          "p",
          { class: records[name]?.error ? "a-inline-error" : "a-subtle" },
          `${homeText("src_" + (sourceKey[name] || name))} · ${scopeText} · ${readText(records[name])}`,
        ),
      );
    }
    clear(
      body,
      card(
        { className: "is-flush" },
        h(
          "div.a-inbox-filter",
          chips(
            [
              { value: null, label: t("everything"), count: rows.length },
              ...groups.map((g) => ({
                value: g.source,
                label: g.label,
                count: g.count,
              })),
            ],
            filter,
            (value) => {
              filter = value;
              drawAll();
            },
            t("filter"),
          ),
        ),
        !Object.keys(records).length
          ? skeleton(5)
          : rows.length
            ? list
            : empty(t("none"), { iconName: "check" }),
      ),
      disclosure(t("scope"), gaps),
    );
  }
  function openRow(row) {
    if (row.source === "automatic")
      return ctx.navigate("vaults", "detail", {
        vault: row.vault_id,
        tab: "automation",
      });
    if (row.source === "videos")
      return ctx.navigate("inventory", "", {
        tab: "video",
        client: row.client_id,
        vault: row.vault_id,
      });
    const target = Object.entries(sectionSources).find(([, sources]) =>
      sources.includes(row.source),
    )?.[0];
    if (target) ctx.navigate("worklist", target, { id: row.id });
    else if (row.page === "imports")
      ctx.navigate("imports", "", {
        client: row.client_id,
        vault: row.vault_id,
      });
    else ctx.navigate(row.page);
  }
  async function mountSection() {
    const mine = ++generation;
    section?.dispose?.();
    section = null;
    if (!sub) {
      drawAll();
      return;
    }
    clear(body, skeleton(4));
    const module = await loaders[sub]();
    if (mine !== generation) return;
    section = module.create(ctx, params, () => loader.refresh(true));
    clear(body, section.node);
    await section.refresh();
  }
  loader.start();
  void mountSection();
  return {
    node: root,
    async refresh() {
      await Promise.all([loader.refresh(true), section?.refresh()]);
    },
    update(next) {
      sub = next.sub || "";
      params = next.params || {};
      drawFrame();
      void mountSection();
      return true;
    },
    dirty: () => Boolean(section?.dirty?.()),
    dispose() {
      generation++;
      cancelAnimationFrame(frame);
      section?.dispose?.();
      loader.dispose();
    },
  };
}
