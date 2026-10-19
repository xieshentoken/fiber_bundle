import { phrase, setText, translate, uiText } from "./i18n/index.js";
// Current scope projection and detail/reviews; layout never writes files or starts analysis.
import { $, el, action, captureMessage } from "./ui.js";
import { control } from "./record_ui.js";
import { captureDraftSave, hasUnsavedChanges } from "./unsaved_changes.js";

export function createGraphCanvas(api, scope) {
  const panel = $("graph-canvas-panel"),
    canvas = $("graph-canvas"),
    detail = $("graph-node-detail"),
    statistics = $("graph-statistics");
  let renderer = null,
    data = null,
    revision = 0,
    detailRevision = 0,
    selected = null;
  const notify = () => captureMessage("graph");
  function dispose() {
    revision++;
    detailRevision++;
    renderer?.kill();
    renderer = null;
    data = null;
    selected = null;
    canvas.replaceChildren();
    detail.replaceChildren();
    setText(statistics, uiText(""));
    $("graph-node-list").replaceChildren();
  }
  function listMode() {
    dispose();
    panel.hidden = true;
    $("graph-content").hidden = false;
    $("graph-filter").closest("label").hidden = false;
    $("graph-list-mode").setAttribute("aria-pressed", "true");
    $("graph-map-mode").setAttribute("aria-pressed", "false");
  }
  action($("graph-list-mode"), async () => {
    if (
      hasUnsavedChanges(detail) &&
      !window.confirm(translate("离开将丢弃未保存审核理由。继续？"))
    )
      return;
    listMode();
  });
  action($("graph-map-mode"), async () => {
    panel.hidden = false;
    $("graph-content").hidden = true;
    $("graph-filter").closest("label").hidden = true;
    $("graph-list-mode").setAttribute("aria-pressed", "false");
    $("graph-map-mode").setAttribute("aria-pressed", "true");
    await load();
  });
  async function load(focus = null) {
    if (panel.hidden) return;
    if (
      hasUnsavedChanges(detail) &&
      !window.confirm(translate("刷新将丢弃未保存审核理由。继续？"))
    )
      return;
    dispose();
    const turn = ++revision,
      fixed = scope();
    setText(statistics, uiText("正在核对当前来源与跨批次图数据…"));
    try {
      const value = await api.projection(
        fixed.client_id,
        fixed.vault_id,
        focus,
      );
      if (turn !== revision) return;
      data = value;
      const stats = value.statistics;
      setText(
        statistics,
        phrase(
          "登记笔记 {p0} · 历史分析覆盖 {p1} · 当前画面已核对分析 {p2} · 显示 {p3} 节点 / {p4} 边 · 显式链接 {p5} / 语义线索 {p6}{p7}",
          {
            p0: stats.registered_notes,
            p1: stats.analyzed_recorded,
            p2: stats.analyzed_current_visible,
            p3: stats.displayed_nodes,
            p4: stats.displayed_edges,
            p5: stats.explicit_links,
            p6: stats.semantic_links,
            p7: stats.partial
              ? uiText("；有界局部视图，可搜索并展开邻居")
              : uiText(""),
          },
        ),
      );
      const fallback = $("graph-node-list");
      fallback.replaceChildren();
      for (const node of value.nodes.filter((n) => n.kind === "note"))
        fallback.append(
          control(
            node.label,
            async () => {
              renderer?.focus(node.id);
              await show(node.id);
            },
            { localize: false },
          ),
        );
      const library = await import("/static/graph_renderer.bundle.js");
      if (turn !== revision) return;
      renderer = library.createGraphRenderer(canvas, value, {
        onSelect: (id) =>
          show(id).catch((error) => notify()(error.message, true)),
        onFailure: (message) => notify()(message, true),
        reducedMotion:
          document.documentElement.dataset.motion === "reduce" ||
          window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      });
      if (focus) {
        renderer?.focus(focus);
        await show(focus);
      }
    } catch (error) {
      if (turn === revision) {
        canvas.replaceChildren(
          el(
            "p",
            uiText("图谱暂不可用；可继续使用建议列表，或先更新本库索引。"),
          ),
        );
        notify()(error.message, true);
      }
      throw error;
    }
  }
  async function show(id) {
    if (!data) return;
    if (
      hasUnsavedChanges(detail) &&
      !window.confirm(translate("切换笔记将丢弃未保存理由。继续？"))
    )
      return;
    const turn = ++detailRevision,
      fixed = scope(),
      snapshot = data;
    const node = snapshot.nodes.find((n) => n.id === id);
    if (!node) return;
    selected = id;
    detail.replaceChildren(el("h3", node.label));
    const neighbors = snapshot.edges.filter(
      (e) => e.source === id || e.target === id,
    );
    if (node.kind === "note") {
      const value = await api.node(fixed.client_id, fixed.vault_id, id);
      if (turn !== detailRevision || data !== snapshot) return;
      detail.append(
        el("p", value.path),
        el("p", value.source_kind),
        el("pre", value.excerpt),
      );
      detail.append(
        control("打开原笔记", async (notify) => {
          if (window.fiberDesktop?.openNote)
            await window.fiberDesktop.openNote({
              clientId: fixed.client_id,
              vaultId: fixed.vault_id,
              noteId: id,
            });
          else notify("Web 原笔记位置：" + value.absolute_path);
        }),
      );
    }
    const groupIds = neighbors.map((e) =>
      e.source === id ? e.target : e.source,
    );
    const groups = snapshot.nodes.filter(
      (n) => n.kind === "group" && groupIds.includes(n.id),
    );
    detail.append(
      el(
        "p",
        phrase("所属分组：{p0}", {
          p0: groups.map((g) => g.label).join("、") || "当前图中暂无分组",
        }),
      ),
    );
    if (node.kind === "note")
      detail.append(control("展开此笔记邻居", async () => load(id)));
    const keys = new Set(neighbors.flatMap((e) => e.candidates));
    for (const key of keys) {
      const candidate = snapshot.candidates[key];
      const card = el("article", null, "record-card");
      card.dataset.draft = "tracked";
      const reason = el("textarea");
      reason.value = candidate.review.reason;
      reason.maxLength = 400;
      setText(reason, uiText("核对来源后填写理由"), "placeholder");
      reason.required = true;
      card.append(
        el("p", `${candidate.type} · ${candidate.review.state}`),
        reason,
      );
      for (const [label, state] of [
        ["接受线索", "accepted"],
        ["拒绝", "rejected"],
        ["重新核对", "suggested"],
      ])
        card.append(
          control(label, async (notify) => {
            if (!reason.reportValidity()) return;
            const acknowledge = captureDraftSave(card),
              submitted = reason.value;
            const review = await api.review(candidate.id, {
              ...fixed,
              job_id: candidate.job_id,
              configuration: candidate.configuration,
              revision: candidate.review.revision,
              state,
              reason: submitted,
            });
            candidate.review = review;
            acknowledge();
            notify("审核已保存，接受线索不代表事实验证。");
            if (reason.value !== submitted) return;
            await load(node.kind === "note" ? id : null);
          }),
        );
      detail.append(card);
    }
  }
  action($("graph-map-refresh"), () => load());
  action($("graph-layout-toggle"), async (notify) => {
    if (!renderer) return;
    if (renderer.layout.isRunning()) {
      renderer.layout.stop();
      notify("布局已暂停。");
    } else {
      renderer.layout.start();
      notify("布局已启动，最多运行 8 秒。");
    }
  });
  action($("graph-fit"), async () => renderer?.fit());
  action($("graph-reset-view"), async () => renderer?.reset());
  action($("graph-unpin"), async () => {
    if (selected && renderer?.graph.hasNode(selected))
      renderer.graph.setNodeAttribute(selected, "fixed", false);
  });
  $("graph-map-search-form").addEventListener("submit", (event) =>
    event.preventDefault(),
  );
  action($("graph-map-search"), async () => {
    const input = $("graph-map-search-text");
    if (!input.reportValidity()) return;
    const fixed = scope(),
      turn = revision,
      matches = await api.nodeSearch(
        fixed.client_id,
        fixed.vault_id,
        input.value,
      );
    if (turn !== revision) return;
    const results = $("graph-search-results");
    results.replaceChildren(
      ...matches.map((n) =>
        control(`${n.label} · ${n.path}`, async () => {
          if (!renderer?.focus(n.id)) await load(n.id);
          else await show(n.id);
        }),
      ),
    );
    if (!matches.length)
      results.append(el("p", uiText("当前授权范围内无匹配笔记。")));
  });
  window.addEventListener("pagehide", dispose);
  return {
    refreshIfVisible: () => load(),
    clear() {
      listMode();
      $("graph-node-list").replaceChildren();
      $("graph-search-results").replaceChildren();
    },
    invalidate() {
      dispose();
    },
  };
}
