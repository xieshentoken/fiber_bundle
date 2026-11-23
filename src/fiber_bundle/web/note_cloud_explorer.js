import { el } from "./ui.js";
import { sameGraphScope } from "./graph_scope_preferences.js";
import { noteCloudMatches } from "./note_cloud_model.js";
import { cloudText as t } from "./note_cloud_copy.js";
import { joinText, setText } from "./i18n/index.js";
import {
  cloudNodeLabel,
  cloudNodeKind,
  cloudNodeFacts,
  cloudEdgeFacts,
} from "./note_cloud_presentation.js";

// Owns visual selection, WebGL lifetime, and bounded accessible navigation.
export function createNoteCloudExplorer({
  button,
  scope,
  onFeedback,
  loadRenderer,
}) {
  const host = el("div", null, "nc-explorer"),
    stage = el("div", null, "nc-stage"),
    canvas = el("div", null, "nc-canvas"),
    tooltip = el("div", null, "nc-tooltip"),
    detail = el("aside", null, "nc-detail"),
    list = el("details", null, "nc-list"),
    listHost = el("div", null, "nc-list-items"),
    paging = el("div", null, "nc-paging");
  let data = null,
    renderer = null,
    selected = null,
    listPage = 0,
    epoch = 0,
    current = () => false;
  tooltip.setAttribute("role", "tooltip");
  tooltip.hidden = true;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", t("title"));
  canvas.tabIndex = 0;
  detail.setAttribute("aria-label", t("related", { count: "" }));
  const fit = button(t("fit"), () => renderer?.fit()),
    zoomIn = button("+", () => renderer?.zoomIn()),
    zoomOut = button("−", () => renderer?.zoomOut()),
    clear = button(t("clear"), () => clearSelection());
  zoomIn.setAttribute("aria-label", t("zoomIn"));
  zoomOut.setAttribute("aria-label", t("zoomOut"));
  const tools = [zoomOut, zoomIn, fit, clear];
  stage.append(canvas, tooltip);
  host.append(stage, detail);
  list.append(el("summary", t("list")), listHost, paging);
  function destroyGraph() {
    renderer?.kill();
    renderer = null;
    canvas.replaceChildren();
    tooltip.hidden = true;
    for (const node of tools) node.disabled = true;
  }
  function emptySelection() {
    selected = null;
    onFeedback("");
    detail.replaceChildren(el("p", t("pick"), "nc-muted"));
    for (const item of listHost.querySelectorAll("button"))
      item.removeAttribute("aria-current");
  }
  function clearSelection() {
    renderer?.clearSelection();
    emptySelection();
    tooltip.hidden = true;
  }
  function reset() {
    epoch++;
    current = () => false;
    destroyGraph();
    data = null;
    listPage = 0;
    emptySelection();
    listHost.replaceChildren();
    paging.replaceChildren();
  }
  function tooltipAt(value) {
    if (!value || !data?.byId.has(value.id)) {
      tooltip.hidden = true;
      return;
    }
    const node = data.byId.get(value.id);
    setText(
      tooltip,
      joinText([cloudNodeKind(node), cloudNodeLabel(node)], " · "),
    );
    tooltip.hidden = false;
    const width = stage.clientWidth || 600,
      height = stage.clientHeight || 500;
    tooltip.style.left = `${Math.max(8, Math.min(value.x + 14, width - (tooltip.offsetWidth || 220) - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(value.y + 14, height - (tooltip.offsetHeight || 50) - 8))}px`;
  }
  function pick(id, { focus = true } = {}) {
    const node = data?.byId.get(id);
    if (!node) return;
    selected = id;
    if (focus && renderer?.focusNode(id)) return;
    const title = el("h2", cloudNodeLabel(node));
    const neighbors = [...data.adjacency.get(id)].map(([key, edges]) => ({
      node: data.byId.get(key),
      edges,
    }));
    const header = el("span", cloudNodeKind(node), "nc-eyebrow");
    detail.replaceChildren(header, title, cloudNodeFacts(node));
    if (node.note_id && window.fiberDesktop?.openNote) {
      const fixed = scope(),
        generation = epoch;
      const open = button(t("open"), async () => {
        // A detached button may still finish; never start an old-scope native request.
        if (
          !current() ||
          generation !== epoch ||
          !sameGraphScope(fixed, scope())
        )
          return;
        open.disabled = true;
        try {
          await window.fiberDesktop.openNote({
            clientId: fixed.client_id,
            vaultId: fixed.vault_id,
            noteId: node.note_id,
          });
        } catch (error) {
          if (current() && generation === epoch)
            onFeedback(t("openFailed", { message: error.message }));
        } finally {
          if (current() && generation === epoch) open.disabled = false;
        }
      });
      detail.append(open);
    } else if (["note", "moc"].includes(node.kind))
      detail.append(
        el("p", t(node.note_id ? "browserPath" : "pathOnly"), "nc-muted"),
      );
    detail.append(el("h3", t("related", { count: neighbors.length })));
    if (!neighbors.length) detail.append(el("p", t("noRelated"), "nc-muted"));
    const relations = el("div", null, "nc-neighbors");
    let neighborPage = 0;
    function paintNeighbors() {
      relations.replaceChildren();
      for (const { node: neighbor, edges } of neighbors.slice(
        neighborPage * 50,
        (neighborPage + 1) * 50,
      )) {
        const link = button(
          cloudNodeLabel(neighbor),
          () => pick(neighbor.id),
          "nc-note-button",
        );
        link.title = neighbor.path;
        const row = el("div", null, "nc-neighbor");
        row.append(
          link,
          el("span", cloudNodeKind(neighbor), "nc-relation-kind"),
          cloudEdgeFacts(edges),
        );
        relations.append(row);
      }
      if (neighbors.length > 50) {
        const previous = button(t("previous"), () => {
          neighborPage--;
          paintNeighbors();
        });
        const next = button(t("next"), () => {
          neighborPage++;
          paintNeighbors();
        });
        previous.disabled = neighborPage === 0;
        next.disabled = (neighborPage + 1) * 50 >= neighbors.length;
        const nav = el("div", null, "nc-paging");
        nav.append(
          previous,
          el(
            "span",
            t("page", {
              page: neighborPage + 1,
              pages: Math.ceil(neighbors.length / 50),
            }),
          ),
          next,
        );
        relations.append(nav);
      }
    }
    paintNeighbors();
    detail.append(relations);
    onFeedback(t("selected", { title: cloudNodeLabel(node) }));
    for (const item of listHost.querySelectorAll("button")) {
      if (item.dataset.noteId === id) item.setAttribute("aria-current", "true");
      else item.removeAttribute("aria-current");
    }
  }
  function paintList() {
    const nodes = data ? noteCloudMatches(data) : [],
      size = 40;
    listPage = Math.min(
      listPage,
      Math.max(0, Math.ceil(nodes.length / size) - 1),
    );
    listHost.replaceChildren();
    for (const node of nodes.slice(listPage * size, (listPage + 1) * size)) {
      const item = button(
        joinText([cloudNodeKind(node), cloudNodeLabel(node)], " · "),
        () => pick(node.id),
        "nc-note-button",
      );
      item.dataset.noteId = node.id;
      item.title = node.path;
      if (node.id === selected) item.setAttribute("aria-current", "true");
      listHost.append(item);
    }
    const previous = button(t("previous"), () => {
      listPage--;
      paintList();
    });
    const next = button(t("next"), () => {
      listPage++;
      paintList();
    });
    previous.disabled = listPage === 0;
    next.disabled = (listPage + 1) * size >= nodes.length;
    paging.replaceChildren(
      previous,
      el(
        "span",
        t("page", {
          page: listPage + 1,
          pages: Math.max(1, Math.ceil(nodes.length / size)),
        }),
      ),
      next,
    );
  }
  async function setData(next, { focus = null, admitted = () => true } = {}) {
    reset();
    data = next;
    const mine = ++epoch;
    current = () => mine === epoch && admitted();
    paintList();
    if (!data.nodes.length) {
      canvas.append(el("p", t("empty"), "nc-empty"));
      return;
    }
    try {
      const library = await loadRenderer();
      if (!current()) return;
      renderer = library.createNoteCloudRenderer({
        container: canvas,
        onHover: (value) => {
          if (current()) tooltipAt(value);
        },
        onSelect: (id) => {
          if (current()) id ? pick(id, { focus: false }) : emptySelection();
        },
        onError: (code) => {
          if (current() && code === "webgl") onFeedback(t("fallback"));
        },
        reducedMotion:
          document.documentElement.dataset.motion === "reduce" ||
          window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
      });
      renderer.setData(data);
      for (const node of tools) node.disabled = false;
    } catch {
      if (!current()) return;
      destroyGraph();
      canvas.append(el("p", t("fallback"), "nc-empty"));
      list.open = true;
    }
    if (current() && focus) pick(focus);
  }
  destroyGraph();
  emptySelection();
  return {
    node: host,
    list,
    tools,
    setData,
    clear: reset,
    clearSelection,
    select: pick,
    hasNode: (id) => !!data?.byId.has(id),
  };
}
