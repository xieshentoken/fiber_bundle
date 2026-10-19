// Canvas/positions are presentation only. Sources, reviews and opening notes stay behind HTTP.
import Sigma from "sigma";
import { MultiUndirectedGraph } from "graphology";
import { createLayout } from "./layout.js";

function palette(container) {
  const css = window.getComputedStyle(container);
  const read = (name) => css.getPropertyValue(name).trim();
  return {
    explicit: read("--graph-explicit"),
    suggested: read("--graph-suggested"),
    accepted: read("--graph-accepted"),
    group: read("--graph-group"),
    node: read("--graph-node"),
    muted: read("--graph-muted"),
    text: read("--text"),
    font: css.getPropertyValue("--font-ui").trim(),
  };
}
export function createGraphRenderer(
  container,
  data,
  { onSelect, onFailure, reducedMotion = false },
) {
  let colors = palette(container);
  const graph = new MultiUndirectedGraph();
  data.nodes.forEach((node, index) => {
    const angle = index * Math.PI * (3 - Math.sqrt(5)),
      radius = Math.sqrt(index + 1);
    graph.addNode(node.id, {
      ...node,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
      size: node.kind === "group" ? 7 : 4,
      color: node.kind === "group" ? colors.group : colors.node,
      fixed: false,
    });
  });
  for (const edge of data.edges)
    graph.addUndirectedEdgeWithKey(edge.id, edge.source, edge.target, {
      ...edge,
      size: { suggested: 0.8, explicit: 1.6, accepted: 2.6 }[edge.kind] || 1.1,
      color: colors[edge.kind],
      weight: edge.kind === "suggested" ? 0.4 : 1,
    });
  let renderer;
  try {
    renderer = new Sigma(graph, container, {
      allowInvalidContainer: true,
      renderEdgeLabels: false,
      labelFont: colors.font,
      labelColor: { color: colors.text },
      labelSize: 12,
      labelDensity: 0.2,
      enableEdgeEvents: false,
    });
  } catch (error) {
    onFailure("WebGL 不可用，请使用笔记列表。");
    throw error;
  }
  const layout = createLayout(graph, onFailure);
  let selected = null,
    hovered = null,
    dragged = null,
    disposed = false;
  function highlight(node) {
    hovered = node;
    renderer.refresh();
  }
  renderer.setSetting("nodeReducer", (node, attributes) => {
    const chosen = hovered || selected;
    if (chosen && chosen !== node && !graph.hasEdge(chosen, node))
      return { ...attributes, color: colors.muted, label: "" };
    return {
      ...attributes,
      highlighted: chosen === node,
      forceLabel: chosen === node,
    };
  });
  renderer.setSetting("edgeReducer", (edge, attributes) => {
    const chosen = hovered || selected;
    return chosen && !graph.extremities(edge).includes(chosen)
      ? { ...attributes, hidden: true }
      : attributes;
  });
  renderer.on("enterNode", ({ node }) => highlight(node));
  renderer.on("leaveNode", () => highlight(null));
  renderer.on("clickNode", ({ node }) => {
    selected = node;
    renderer.refresh();
    onSelect(node);
  });
  renderer.on("downNode", ({ node }) => {
    layout.stop();
    dragged = node;
    graph.setNodeAttribute(node, "fixed", true);
  });
  const captor = renderer.getMouseCaptor();
  captor.on("mousemovebody", (event) => {
    if (!dragged) return;
    const position = renderer.viewportToGraph(event);
    graph.mergeNodeAttributes(dragged, position);
    event.preventSigmaDefault();
    event.original.preventDefault();
    event.original.stopPropagation();
  });
  captor.on("mouseup", () => {
    dragged = null;
  });
  if (!reducedMotion) layout.start();
  const resize = new ResizeObserver(() => {
    if (!disposed) renderer.resize();
  });
  resize.observe(container);
  // Recolor the existing graph in place. Camera, pinning, selection and layout stay intact.
  const appearance = new window.MutationObserver(() => {
    if (disposed) return;
    colors = palette(container);
    graph.forEachNode((node, attributes) =>
      graph.setNodeAttribute(
        node,
        "color",
        attributes.kind === "group" ? colors.group : colors.node,
      ),
    );
    graph.forEachEdge((edge, attributes) =>
      graph.setEdgeAttribute(edge, "color", colors[attributes.kind]),
    );
    renderer.setSetting("labelFont", colors.font);
    renderer.setSetting("labelColor", { color: colors.text });
    reducedMotion =
      document.documentElement.dataset.motion === "reduce" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) layout.stop();
    renderer.refresh();
  });
  appearance.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme", "style", "data-font-ready", "data-motion"],
  });
  return {
    graph,
    renderer,
    layout,
    focus(id) {
      if (!graph.hasNode(id)) return false;
      selected = id;
      renderer.refresh();
      const coordinates = renderer.getNodeDisplayData(id);
      renderer
        .getCamera()
        .setState({ x: coordinates.x, y: coordinates.y, ratio: 0.25 });
      return true;
    },
    fit() {
      renderer.getCamera().setState({ x: 0.5, y: 0.5, ratio: 1 });
    },
    reset() {
      layout.stop();
      graph.forEachNode((node) => {
        graph.setNodeAttribute(node, "fixed", false);
      });
      renderer.getCamera().setState({ x: 0.5, y: 0.5, ratio: 1 });
      if (!reducedMotion) layout.start();
    },
    kill() {
      disposed = true;
      resize.disconnect();
      appearance.disconnect();
      layout.kill();
      renderer.kill();
    },
  };
}
