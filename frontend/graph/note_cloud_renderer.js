// Canvas positions are presentation only. All note access stays behind HTTP.
import Sigma from "sigma";
import { NodeCircleProgram } from "sigma/rendering";
import {
  buildCloudGraph,
  cloudNeighborhood,
  CLOUD_COLORS,
  cloudNodeColor,
  CLOUD_UNRESOLVED_COLOR,
} from "./note_cloud_model.js";
import { createCloudLayout } from "./note_cloud_layout.js";
import { createCloudNodePrograms } from "./note_cloud_programs.js";

function palette(container) {
  const css = window.getComputedStyle(container);
  const read = (name, fallback) =>
    css.getPropertyValue(name).trim() || fallback;
  return {
    groups: CLOUD_COLORS.map((color, index) =>
      read(`--note-cloud-node-${index + 1}`, color),
    ),
    edge: read("--note-cloud-edge", "#d8d6cb"),
    activeEdge: read("--note-cloud-active-edge", "#8e9d85"),
    muted: read("--note-cloud-muted", "#e7e4db"),
    unresolved: read("--note-cloud-unresolved", CLOUD_UNRESOLVED_COLOR),
    text: read("--text", "#34362f"),
    font: read("--font-ui", "sans-serif"),
  };
}

export function createNoteCloudRenderer({
  container,
  onHover = () => {},
  onSelect = () => {},
  onError = () => {},
  reducedMotion = false,
}) {
  let colors = palette(container);
  let graph = buildCloudGraph({ nodes: [], edges: [] }, colors.groups);
  let renderer;
  try {
    renderer = new Sigma(graph, container, {
      allowInvalidContainer: true,
      renderLabels: false,
      defaultDrawNodeHover: () => {},
      renderEdgeLabels: false,
      labelFont: colors.font,
      labelColor: { color: colors.text },
      labelSize: 12,
      labelDensity: 0.1,
      labelRenderedSizeThreshold: 0,
      enableEdgeEvents: false,
      stagePadding: 36,
      minCameraRatio: 0.025,
      maxCameraRatio: 4,
      zIndex: true,
      defaultEdgeColor: colors.edge,
      nodeProgramClasses: createCloudNodePrograms(NodeCircleProgram),
    });
  } catch (error) {
    container.replaceChildren();
    onError("webgl");
    throw error;
  }
  let layout = createCloudLayout(graph, onError);
  let hovered = null,
    selected = null,
    neighborhood = new Set(),
    disposed = false;

  function emitHover() {
    if (disposed || !hovered || !graph.hasNode(hovered)) return;
    const attributes = graph.getNodeAttributes(hovered);
    const point = renderer.graphToViewport(attributes);
    onHover({ id: hovered, label: attributes.label, x: point.x, y: point.y });
  }
  function highlight() {
    neighborhood = cloudNeighborhood(graph, hovered || selected);
    renderer.refresh();
  }
  renderer.setSetting("nodeReducer", (id, attributes) => {
    const chosen = hovered || selected;
    const active = chosen === id;
    const related = neighborhood.has(id);
    return {
      ...attributes,
      color: chosen && !related ? colors.muted : attributes.color,
      label: active ? attributes.label : "",
      size: active ? attributes.size + 1.8 : attributes.size,
      highlighted: active,
      forceLabel: active,
      zIndex: active ? 2 : related ? 1 : 0,
    };
  });
  renderer.setSetting("edgeReducer", (id, attributes) => {
    const chosen = hovered || selected;
    const incident = chosen && graph.extremities(id).includes(chosen);
    return {
      ...attributes,
      color: incident ? colors.activeEdge : colors.edge,
      size: incident ? 1.3 : attributes.size,
      hidden: Boolean(chosen && !incident),
      zIndex: incident ? 1 : 0,
    };
  });
  renderer.on("enterNode", ({ node }) => {
    hovered = node;
    highlight();
    emitHover();
    container.style.cursor = "pointer";
  });
  renderer.on("leaveNode", () => {
    hovered = null;
    onHover(null);
    highlight();
    container.style.cursor = "grab";
  });
  renderer.on("clickNode", ({ node }) => {
    layout.stop();
    selected = node;
    highlight();
    onSelect(node);
  });
  function clearSelection() {
    if (disposed) return;
    selected = null;
    hovered = null;
    onHover(null);
    highlight();
    onSelect(null);
  }
  renderer.on("clickStage", clearSelection);
  renderer.on("afterRender", emitHover);
  container.style.cursor = "grab";
  const resize = new ResizeObserver(() => {
    if (!disposed) renderer.resize();
  });
  resize.observe(container);
  const appearance = new window.MutationObserver(() => {
    if (disposed) return;
    colors = palette(container);
    graph.forEachNode((id, attributes) =>
      graph.setNodeAttribute(
        id,
        "color",
        cloudNodeColor(attributes, colors.groups, colors.unresolved),
      ),
    );
    renderer.setSetting("labelFont", colors.font);
    renderer.setSetting("labelColor", { color: colors.text });
    if (
      document.documentElement.dataset.motion === "reduce" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      layout.stop();
    renderer.refresh();
  });
  appearance.observe(document.documentElement, {
    attributes: true,
    attributeFilter: [
      "data-theme",
      "data-mode",
      "style",
      "data-font-ready",
      "data-motion",
    ],
  });
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const motionChange = () => {
    if (motionQuery.matches) layout.stop();
  };
  motionQuery.addEventListener("change", motionChange);

  return {
    get graph() {
      return graph;
    },
    renderer,
    setData(data) {
      if (disposed) return;
      // Validate before replacing the previous graph. Never append a late
      // snapshot to a graph from another scope.
      const next = buildCloudGraph(data, colors.groups);
      next.forEachNode((id, attributes) =>
        next.setNodeAttribute(
          id,
          "color",
          cloudNodeColor(attributes, colors.groups, colors.unresolved),
        ),
      );
      layout.kill();
      graph = next;
      layout = createCloudLayout(graph, onError);
      hovered = null;
      selected = null;
      neighborhood = new Set();
      onHover(null);
      renderer.setGraph(graph);
      renderer.getCamera().setState({ x: 0.5, y: 0.5, ratio: 1, angle: 0 });
      if (
        !reducedMotion &&
        !motionQuery.matches &&
        document.documentElement.dataset.motion !== "reduce"
      )
        layout.start();
    },
    focusNode(id) {
      if (disposed || !graph.hasNode(id)) return false;
      layout.stop();
      selected = id;
      hovered = null;
      onHover(null);
      highlight();
      const point = renderer.getNodeDisplayData(id);
      if (point)
        renderer.getCamera().setState({ x: point.x, y: point.y, ratio: 0.65 });
      onSelect(id);
      return true;
    },
    fit() {
      if (!disposed)
        renderer.getCamera().setState({ x: 0.5, y: 0.5, ratio: 1, angle: 0 });
    },
    zoomIn() {
      if (!disposed)
        renderer
          .getCamera()
          .updateState((state) => ({ ratio: state.ratio / 1.5 }));
    },
    zoomOut() {
      if (!disposed)
        renderer
          .getCamera()
          .updateState((state) => ({ ratio: state.ratio * 1.5 }));
    },
    clearSelection,
    kill() {
      if (disposed) return;
      disposed = true;
      layout.kill();
      resize.disconnect();
      appearance.disconnect();
      motionQuery.removeEventListener("change", motionChange);
      onHover(null);
      renderer.kill();
      container.style.cursor = "";
    },
  };
}
