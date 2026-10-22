// Note relationship map: current-scope projection, node detail and reviews. Layout never
// writes files or starts analysis. Rendering uses the packaged graph bundle.
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  empty,
  failure,
  field,
  input,
  notice,
  textarea,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { captureDraftSave, hasUnsavedChanges } from "../unsaved_changes.js";

const t = strings({
  zh: {
    loading: "正在核对当前来源与跨批次图数据…",
    stats:
      "登记笔记 {r} · 历史分析覆盖 {a} · 当前已核对分析 {v} · 显示 {n} 节点 / {e} 边 · 显式链接 {x} / 语义线索 {s}",
    partial: "有界局部视图，可搜索并展开邻居",
    legend:
      "蓝色中等线：原文显式链接 · 灰色细线：待审核关联 · 深色粗线：已接受关联 · 大节点：分组。位置不表示可信度或语义距离。",
    unavailable: "图谱暂不可用；可继续使用建议列表，或先更新本库索引。",
    search: "搜索笔记",
    find: "查找",
    noMatch: "当前授权范围内无匹配笔记。",
    layout: "开始 / 暂停布局",
    layoutStarted: "布局已启动，最多运行 8 秒。",
    layoutStopped: "布局已暂停。",
    fit: "适应画面",
    reset: "重置视图",
    unpin: "取消固定所选",
    reload: "重新读取",
    pick: "在图中或列表中选择一篇笔记查看详情。",
    open: "打开原笔记",
    webPath: "Web 原笔记位置：{path}",
    groups: "所属分组：{list}",
    noGroups: "当前图中暂无分组",
    expand: "展开此笔记邻居",
    reasonHint: "核对来源后填写理由",
    accept: "接受线索",
    reject: "拒绝",
    recheck: "重新核对",
    saved: "审核已保存，接受线索不代表事实验证。",
    discard: "将丢弃未保存的审核理由。继续？",
    notes: "笔记（无障碍列表）",
  },
  en: {
    loading: "Checking current sources and cross-batch graph data…",
    stats:
      "{r} registered notes · {a} analysed historically · {v} currently verified · showing {n} nodes / {e} edges · {x} explicit links / {s} semantic hints",
    partial: "Bounded partial view — search and expand neighbours",
    legend:
      "Blue medium line: explicit link in the text · thin grey: link to review · thick dark: accepted link · large nodes: groups. Position doesn't mean confidence or semantic distance.",
    unavailable:
      "The map isn't available; keep using the suggestion list, or update the vault index first.",
    search: "Search notes",
    find: "Find",
    noMatch: "No matching notes in the authorized scope.",
    layout: "Start / pause layout",
    layoutStarted: "Layout started (runs up to 8 s).",
    layoutStopped: "Layout paused.",
    fit: "Fit to view",
    reset: "Reset view",
    unpin: "Unpin selected",
    reload: "Reload",
    pick: "Pick a note in the map or list to see details.",
    open: "Open original note",
    webPath: "Note location: {path}",
    groups: "Groups: {list}",
    noGroups: "no groups in this view",
    expand: "Expand neighbours",
    reasonHint: "Give a reason after checking the source",
    accept: "Accept hint",
    reject: "Reject",
    recheck: "Re-check",
    saved: "Review saved. Accepting a hint doesn't verify facts.",
    discard: "Unsaved review reasons will be discarded. Continue?",
    notes: "Notes (accessible list)",
  },
});

export function createGraphMap(graph, scope) {
  const canvas = h("div.a-graph-canvas");
  const detail = h("div.a-stack.a-graph-detail");
  const stats = h("p.a-subtle", { role: "status" });
  const nodeList = h("div.a-graph-nodes");
  const results = h("div.a-stack");
  const query = input({
    required: true,
    maxLength: 200,
    placeholder: t("search"),
    dataset: { draft: "ignore" },
  });
  let renderer = null,
    data = null,
    revision = 0,
    detailRevision = 0,
    selected = null;
  function dispose() {
    revision++;
    detailRevision++;
    renderer?.kill();
    renderer = null;
    data = null;
    selected = null;
    canvas.replaceChildren();
    clear(detail, empty(t("pick"), { iconName: "graph" }));
    stats.textContent = "";
    nodeList.replaceChildren();
  }
  async function load(focus = null) {
    if (hasUnsavedChanges(detail) && !window.confirm(t("discard"))) return;
    dispose();
    const turn = ++revision,
      fixed = scope();
    stats.textContent = t("loading");
    try {
      const value = await graph.projection(
        fixed.client_id,
        fixed.vault_id,
        focus,
      );
      if (turn !== revision) return;
      data = value;
      const s = value.statistics;
      stats.textContent =
        t("stats", {
          r: s.registered_notes,
          a: s.analyzed_recorded,
          v: s.analyzed_current_visible,
          n: s.displayed_nodes,
          e: s.displayed_edges,
          x: s.explicit_links,
          s: s.semantic_links,
        }) + (s.partial ? " · " + t("partial") : "");
      clear(
        nodeList,
        value.nodes
          .filter((n) => n.kind === "note")
          .map((node) => {
            const pick = h("button.a-link", { type: "button" }, node.label);
            pick.addEventListener("click", () =>
              run(pick, async () => {
                renderer?.focus(node.id);
                await show(node.id);
              }),
            );
            return pick;
          }),
      );
      const library = await import("/static/graph_renderer.bundle.js");
      if (turn !== revision) return;
      renderer = library.createGraphRenderer(canvas, value, {
        onSelect: (id) =>
          show(id).catch((error) => toast(error.message, { tone: "error" })),
        onFailure: (message) => toast(message, { tone: "error" }),
        reducedMotion:
          document.documentElement.dataset.motion === "reduce" ||
          window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      });
      if (focus) {
        renderer?.focus(focus);
        await show(focus);
      }
    } catch (error) {
      if (turn === revision)
        clear(
          canvas,
          failure(error, () => load(focus)),
          notice(t("unavailable"), "info"),
        );
    }
  }
  async function show(id) {
    if (!data) return;
    if (hasUnsavedChanges(detail) && !window.confirm(t("discard"))) return;
    const turn = ++detailRevision,
      fixed = scope(),
      snapshot = data;
    const node = snapshot.nodes.find((n) => n.id === id);
    if (!node) return;
    selected = id;
    const parts = [h("h3", node.label)];
    const neighbors = snapshot.edges.filter(
      (e) => e.source === id || e.target === id,
    );
    if (node.kind === "note") {
      const value = await graph.node(fixed.client_id, fixed.vault_id, id);
      if (turn !== detailRevision || data !== snapshot) return;
      const openNote = button(t("open"), {
        variant: "small",
        iconName: "external",
      });
      openNote.addEventListener("click", () =>
        run(openNote, async () => {
          if (window.fiberDesktop?.openNote)
            await window.fiberDesktop.openNote({
              clientId: fixed.client_id,
              vaultId: fixed.vault_id,
              noteId: id,
            });
          else
            toast(t("webPath", { path: value.absolute_path }), {
              tone: "info",
              timeout: 0,
            });
        }),
      );
      const expand = button(t("expand"), {
        variant: "small",
        iconName: "graph",
      });
      expand.addEventListener("click", () => run(expand, () => load(id)));
      parts.push(
        h("p.a-subtle", value.path),
        h("p.a-subtle", value.source_kind),
        h("pre.a-pre", value.excerpt),
        h("div.a-toolbar", openNote, expand),
      );
    }
    const groupIds = neighbors.map((e) =>
      e.source === id ? e.target : e.source,
    );
    const groups = snapshot.nodes.filter(
      (n) => n.kind === "group" && groupIds.includes(n.id),
    );
    parts.push(
      h(
        "p.a-subtle",
        t("groups", {
          list: groups.map((g) => g.label).join("、") || t("noGroups"),
        }),
      ),
    );
    for (const key of new Set(neighbors.flatMap((e) => e.candidates))) {
      const candidate = snapshot.candidates[key];
      const reason = textarea({
        value: candidate.review.reason,
        maxLength: 400,
        required: true,
        rows: 2,
        placeholder: t("reasonHint"),
      });
      const box = h(
        "div.a-op",
        { dataset: { draft: "tracked" } },
        h("p", h("strong", candidate.type), " · ", candidate.review.state),
        reason,
      );
      const actions = h("div.a-toolbar");
      for (const [label, state] of [
        [t("accept"), "accepted"],
        [t("reject"), "rejected"],
        [t("recheck"), "suggested"],
      ]) {
        const control = button(label, { variant: "small" });
        control.addEventListener("click", () =>
          run(control, async () => {
            if (!reason.reportValidity()) return;
            const acknowledge = captureDraftSave(box),
              submitted = reason.value;
            const review = await graph.review(candidate.id, {
              ...fixed,
              job_id: candidate.job_id,
              configuration: candidate.configuration,
              revision: candidate.review.revision,
              state,
              reason: submitted,
            });
            candidate.review = review;
            acknowledge();
            toast(t("saved"), { tone: "success" });
            if (reason.value !== submitted) return;
            await load(node.kind === "note" ? id : null);
          }),
        );
        actions.append(control);
      }
      box.append(actions);
      parts.push(box);
    }
    clear(detail, ...parts);
  }
  const findButton = button(t("find"), { iconName: "search" });
  findButton.addEventListener("click", () =>
    run(findButton, async () => {
      if (!query.reportValidity()) return;
      const fixed = scope(),
        turn = revision;
      const matches = await graph.nodeSearch(
        fixed.client_id,
        fixed.vault_id,
        query.value,
      );
      if (turn !== revision) return;
      clear(
        results,
        matches.length
          ? matches.map((n) => {
              const pick = h(
                "button.a-link",
                { type: "button" },
                `${n.label} · ${n.path}`,
              );
              pick.addEventListener("click", () =>
                run(pick, async () => {
                  if (!renderer?.focus(n.id)) await load(n.id);
                  else await show(n.id);
                }),
              );
              return pick;
            })
          : h("p.a-subtle", t("noMatch")),
      );
    }),
  );
  const tool = (label, iconName, work) => {
    const control = h(
      "button.a-icon-button",
      { type: "button", title: label, "aria-label": label },
      icon(iconName),
    );
    control.addEventListener("click", () => run(control, work));
    return control;
  };
  const node = h(
    "div.a-stack",
    h(
      "div.a-toolbar",
      h(
        "form.a-graph-search",
        { onsubmit: (event) => (event.preventDefault(), findButton.click()) },
        field(t("search"), query),
        findButton,
      ),
      h("span.a-spacer"),
      tool(t("layout"), "play", async () => {
        if (!renderer) return;
        if (renderer.layout.isRunning()) {
          renderer.layout.stop();
          toast(t("layoutStopped"), { tone: "info", timeout: 2000 });
        } else {
          renderer.layout.start();
          toast(t("layoutStarted"), { tone: "info", timeout: 2000 });
        }
      }),
      tool(t("fit"), "eye", async () => renderer?.fit()),
      tool(t("reset"), "refresh", async () => renderer?.reset()),
      tool(t("unpin"), "link", async () => {
        if (selected && renderer?.graph.hasNode(selected))
          renderer.graph.setNodeAttribute(selected, "fixed", false);
      }),
      tool(t("reload"), "layers", () => load()),
    ),
    results,
    stats,
    h("p.a-subtle.a-graph-legend", t("legend")),
    h("div.a-graph-layout", canvas, detail),
    h(
      "details.a-disclosure",
      h("summary", t("notes")),
      h("div.a-disclosure-body", nodeList),
    ),
  );
  clear(detail, empty(t("pick"), { iconName: "graph" }));
  return { node, load, dispose, dirty: () => hasUnsavedChanges(detail) };
}
