import { MultiUndirectedGraph } from "graphology";

export const CLOUD_COLORS = [
  "#94a78d",
  "#b9a5bd",
  "#c29885",
  "#94aeb7",
  "#b7ad79",
  "#b7a392",
  "#9babc4",
];

export const CLOUD_LIMITS = { nodes: 20000, edges: 100000 };
export const CLOUD_UNRESOLVED_COLOR = "#979c98";

export function groupColor(group, colors = CLOUD_COLORS) {
  let hash = 2166136261;
  for (const character of String(group || "notes"))
    hash = Math.imul(hash ^ character.codePointAt(0), 16777619);
  return colors[(hash >>> 0) % colors.length];
}

export function cloudNodeColor(
  node,
  colors = CLOUD_COLORS,
  unresolvedColor = CLOUD_UNRESOLVED_COLOR,
) {
  return node.kind === "unresolved"
    ? unresolvedColor
    : groupColor(node.group, colors);
}

function nodePresentation(kind, dense) {
  if (kind === "unresolved")
    return { type: "cloud-unresolved", size: dense ? 1.65 : 3.2 };
  if (kind === "expert") return { type: "cloud-expert", size: dense ? 2 : 3.7 };
  if (kind === "moc" || kind === "group")
    return { type: "circle", size: dense ? 2.3 : 4 };
  return { type: "circle", size: dense ? 1.15 : 2.6 };
}

// Coordinates and color groups are presentation only; edges come exclusively
// from the snapshot. Reject malformed edges instead of inventing endpoints.
export function buildCloudGraph(data, colors = CLOUD_COLORS) {
  if (!Array.isArray(data?.nodes) || !Array.isArray(data?.edges))
    throw new TypeError("Invalid note cloud snapshot");
  if (
    data.nodes.length > CLOUD_LIMITS.nodes ||
    data.edges.length > CLOUD_LIMITS.edges
  )
    throw new RangeError("Note cloud snapshot exceeds renderer limit");
  const graph = new MultiUndirectedGraph();
  const sorted = [...data.nodes].sort((a, b) =>
    String(a.id).localeCompare(String(b.id)),
  );
  const dense = sorted.length > 1000;
  sorted.forEach((node, index) => {
    if (typeof node.id !== "string" || !node.id || graph.hasNode(node.id))
      throw new TypeError("Invalid or duplicate note cloud node");
    const angle = index * Math.PI * (3 - Math.sqrt(5));
    const radius = Math.sqrt(index + 1) * 2.2;
    const group = node.group ? String(node.group) : "";
    graph.addNode(node.id, {
      id: node.id,
      label: String(node.label || node.title || node.path || node.id),
      path: String(node.path || ""),
      kind: node.kind || "note",
      group,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
      ...nodePresentation(node.kind, dense),
      color: cloudNodeColor({ kind: node.kind, group }, colors),
      fixed: false,
    });
  });
  for (const edge of data.edges) {
    if (
      typeof edge.id !== "string" ||
      !edge.id ||
      graph.hasEdge(edge.id) ||
      !graph.hasNode(edge.source) ||
      !graph.hasNode(edge.target)
    )
      throw new TypeError("Invalid note cloud edge");
    graph.addUndirectedEdgeWithKey(edge.id, edge.source, edge.target, {
      kind: edge.kind || "explicit",
      size: edge.kind === "moc" ? 0.6 : 0.45,
      weight: edge.kind === "moc" ? 0.8 : 1,
      ...(edge.membership !== undefined ? { membership: edge.membership } : {}),
    });
  }
  // MOC proximity and disconnected components influence color only. A
  // deterministic multi-source traversal never adds or removes relationships.
  const groups = new Map();
  const queue = [];
  for (const node of sorted) {
    if (node.group || node.kind === "moc" || node.kind === "group") {
      groups.set(node.id, String(node.group || node.id));
      queue.push(node.id);
    }
  }
  function spread(start) {
    for (let index = start; index < queue.length; index += 1) {
      const id = queue[index];
      graph.forEachNeighbor(id, (neighbor) => {
        if (groups.has(neighbor)) return;
        groups.set(neighbor, groups.get(id));
        queue.push(neighbor);
      });
    }
  }
  spread(0);
  for (const node of sorted) {
    if (groups.has(node.id)) continue;
    const start = queue.length;
    groups.set(node.id, node.id);
    queue.push(node.id);
    spread(start);
  }
  graph.forEachNode((id, attributes) => {
    graph.mergeNodeAttributes(id, {
      group: groups.get(id),
      color: cloudNodeColor(
        { kind: attributes.kind, group: groups.get(id) },
        colors,
      ),
    });
    graph.setNodeAttribute(
      id,
      "size",
      attributes.size +
        Math.min(
          dense ? 0.35 : 0.8,
          Math.sqrt(graph.degree(id)) * (dense ? 0.07 : 0.14),
        ),
    );
  });
  return graph;
}

export function cloudNeighborhood(graph, id) {
  if (!id || !graph.hasNode(id)) return new Set();
  return new Set([id, ...graph.neighbors(id)]);
}
