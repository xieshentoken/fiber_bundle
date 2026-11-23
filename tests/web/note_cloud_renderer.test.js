import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCloudGraph,
  cloudNeighborhood,
  groupColor,
  CLOUD_LIMITS,
  cloudNodeColor,
  CLOUD_UNRESOLVED_COLOR,
} from "../../frontend/graph/note_cloud_model.js";
import { createCloudLayout } from "../../frontend/graph/note_cloud_layout.js";
import {
  createCloudNodePrograms,
  EXPERT_FRAGMENT,
  UNRESOLVED_FRAGMENT,
} from "../../frontend/graph/note_cloud_programs.js";

const fixture = () => ({
  nodes: [
    {
      id: "a",
      label: "材料 / Adhesion",
      path: "材料/a.md",
      group: "materials",
    },
    { id: "b", label: "界面科学", path: "材料/b.md", group: "materials" },
    {
      id: "c",
      label: "Thermal transfer",
      path: "工程/c.md",
      group: "engineering",
    },
    { id: "isolated", label: "尚未分析的独立笔记" },
  ],
  edges: [
    { id: "ab", source: "a", target: "b", kind: "explicit" },
    { id: "bc", source: "b", target: "c", kind: "moc" },
  ],
});

test("cloud graph preserves every supplied note and only supplied relationships", () => {
  const graph = buildCloudGraph(fixture());
  assert.equal(graph.order, 4);
  assert.equal(graph.size, 2);
  assert.equal(graph.degree("isolated"), 0);
  assert.equal(graph.getNodeAttribute("a", "label"), "材料 / Adhesion");
  assert.deepEqual([...cloudNeighborhood(graph, "a")].sort(), ["a", "b"]);
  assert.deepEqual([...cloudNeighborhood(graph, "isolated")], ["isolated"]);
  assert.equal(cloudNeighborhood(graph, "absent").size, 0);
  assert.equal(graph.getEdgeAttribute("bc", "kind"), "moc");
});

test("presentation is finite and stable across snapshot ordering", () => {
  const data = fixture();
  const first = buildCloudGraph(data);
  const second = buildCloudGraph({ ...data, nodes: data.nodes.toReversed() });
  for (const id of first.nodes()) {
    const a = first.getNodeAttributes(id),
      b = second.getNodeAttributes(id);
    assert.equal(a.x, b.x);
    assert.equal(a.y, b.y);
    assert.ok(Number.isFinite(a.x) && Number.isFinite(a.y));
    assert.equal(a.color, b.color);
  }
  assert.equal(groupColor("same"), groupColor("same"));
  assert.equal(
    first.getNodeAttribute("a", "color"),
    first.getNodeAttribute("b", "color"),
  );
});

test("invalid endpoints and duplicate identities fail without fabricating notes", () => {
  const data = fixture();
  assert.throws(
    () =>
      buildCloudGraph({
        ...data,
        edges: [{ id: "bad", source: "a", target: "hidden" }],
      }),
    /Invalid note cloud edge/,
  );
  assert.throws(
    () => buildCloudGraph({ ...data, nodes: [...data.nodes, data.nodes[0]] }),
    /duplicate/,
  );
  assert.throws(
    () => buildCloudGraph({ ...data, edges: [...data.edges, data.edges[0]] }),
    /Invalid note cloud edge/,
  );
  assert.throws(() => buildCloudGraph({ nodes: [], edges: null }), /snapshot/);
  assert.equal(buildCloudGraph({ nodes: [], edges: [] }).order, 0);
});

test("MOC relationships seed presentation groups while disconnected notes remain present", () => {
  const graph = buildCloudGraph({
    nodes: [
      { id: "moc-a", kind: "moc", label: "材料 MOC" },
      { id: "moc-b", kind: "moc", label: "工程 MOC" },
      { id: "a" },
      { id: "b" },
      { id: "c" },
      { id: "isolated" },
    ],
    edges: [
      { id: "ma", source: "moc-a", target: "a", kind: "moc" },
      { id: "mb", source: "moc-b", target: "b", kind: "moc" },
      { id: "ac", source: "a", target: "c", kind: "explicit" },
    ],
  });
  assert.equal(graph.getNodeAttribute("a", "group"), "moc-a");
  assert.equal(graph.getNodeAttribute("c", "group"), "moc-a");
  assert.equal(graph.getNodeAttribute("b", "group"), "moc-b");
  assert.equal(graph.getNodeAttribute("isolated", "group"), "isolated");
  assert.equal(graph.size, 3);
});

test("v2 expert and unresolved nodes retain identity, kind and all factual edges", () => {
  const expert = "expert:00000000-0000-4000-8000-000000000001";
  const missing = "missing:" + "a".repeat(64);
  const membership = { owner: "manual", confirmed: true };
  const data = {
    nodes: [
      { id: "note:one", kind: "note", label: "原始笔记" },
      { id: "moc:one", kind: "moc", label: "材料 MOC" },
      {
        id: expert,
        kind: "expert",
        label: "Adhesion Science",
        path: "expert/reference.md",
      },
      {
        id: missing,
        kind: "unresolved",
        label: "尚待核对的目标",
        path: "missing.md",
      },
    ],
    edges: [
      {
        id: "moc-expert",
        source: "moc:one",
        target: expert,
        kind: "moc",
        membership,
      },
      {
        id: "note-expert",
        source: "note:one",
        target: expert,
        kind: "explicit",
      },
      {
        id: "note-missing",
        source: "note:one",
        target: missing,
        kind: "explicit",
      },
    ],
  };
  const before = structuredClone(data);
  const graph = buildCloudGraph(data);
  assert.equal(graph.order, 4);
  assert.equal(graph.size, 3);
  assert.equal(graph.getNodeAttribute(expert, "kind"), "expert");
  assert.equal(graph.getNodeAttribute(expert, "type"), "cloud-expert");
  assert.equal(graph.getNodeAttribute(missing, "kind"), "unresolved");
  assert.equal(graph.getNodeAttribute(missing, "type"), "cloud-unresolved");
  assert.equal(
    graph.getNodeAttribute(missing, "color"),
    CLOUD_UNRESOLVED_COLOR,
  );
  assert.equal(graph.getNodeAttribute(expert, "group"), "moc:one");
  assert.equal(graph.getNodeAttribute(missing, "group"), "moc:one");
  assert.deepEqual(
    [...cloudNeighborhood(graph, missing)],
    [missing, "note:one"],
  );
  assert.deepEqual(
    graph.getEdgeAttribute("moc-expert", "membership"),
    membership,
  );
  assert.deepEqual(graph.extremities("moc-expert"), ["moc:one", expert]);
  assert.deepEqual(data, before);
});

test("unresolved neutral color survives grouping and palette changes", () => {
  const ghost = { kind: "unresolved", group: "material MOC" };
  assert.equal(cloudNodeColor(ghost, ["#ff0000"]), CLOUD_UNRESOLVED_COLOR);
  assert.equal(cloudNodeColor(ghost, ["#ff0000"], "#aabbcc"), "#aabbcc");
  assert.equal(
    cloudNodeColor({ ...ghost, kind: "expert" }, ["#ff0000"]),
    "#ff0000",
  );
});

test("special glyph adapters preserve Sigma geometry, uniforms and lifecycle", () => {
  const definition = {
    VERTICES: 3,
    VERTEX_SHADER_SOURCE: "circle vertex",
    FRAGMENT_SHADER_SOURCE: "circle fragment",
    UNIFORMS: ["u_sizeRatio", "u_correctionRatio", "u_matrix"],
    ATTRIBUTES: [{ name: "a_position" }],
  };
  class CircleProgram {
    getDefinition() {
      return definition;
    }
    kill() {
      return "released";
    }
  }
  const programs = createCloudNodePrograms(CircleProgram);
  const expert = new programs["cloud-expert"]();
  const unresolved = new programs["cloud-unresolved"]();
  assert.equal(expert.getDefinition().FRAGMENT_SHADER_SOURCE, EXPERT_FRAGMENT);
  assert.equal(
    unresolved.getDefinition().FRAGMENT_SHADER_SOURCE,
    UNRESOLVED_FRAGMENT,
  );
  for (const program of [expert, unresolved]) {
    const output = program.getDefinition();
    assert.equal(output.VERTICES, definition.VERTICES);
    assert.equal(output.VERTEX_SHADER_SOURCE, definition.VERTEX_SHADER_SOURCE);
    assert.equal(output.UNIFORMS, definition.UNIFORMS);
    assert.equal(output.ATTRIBUTES, definition.ATTRIBUTES);
    assert.equal(program.kill(), "released");
  }
  assert.equal(definition.FRAGMENT_SHADER_SOURCE, "circle fragment");
});

test("755 displayed targets comprise 740 notes, seven experts and eight unresolved targets", () => {
  const notes = Array.from({ length: 740 }, (_, i) => ({
    id: `note:${i}`,
    kind: i < 4 ? "moc" : "note",
  }));
  const experts = Array.from({ length: 7 }, (_, i) => ({
    id: `expert:${i}`,
    kind: "expert",
  }));
  const missing = Array.from({ length: 8 }, (_, i) => ({
    id: `missing:${i}`,
    kind: "unresolved",
  }));
  const nodes = [...notes, ...experts, ...missing];
  const edges = nodes
    .slice(1)
    .map((node, i) => ({
      id: `edge:${i}`,
      source: notes[0].id,
      target: node.id,
      kind: "explicit",
    }));
  const graph = buildCloudGraph({ nodes, edges });
  assert.equal(graph.order, 755);
  assert.equal(graph.size, edges.length);
  assert.equal(
    graph.filterNodes((id, attributes) => attributes.kind === "expert").length,
    7,
  );
  assert.equal(
    graph.filterNodes((id, attributes) => attributes.kind === "unresolved")
      .length,
    8,
  );
  assert.equal(
    graph.filterNodes((id, attributes) => attributes.type === "circle").length,
    740,
  );
});

test("input limits reject oversized render requests rather than dropping notes", () => {
  assert.throws(
    () =>
      buildCloudGraph({
        nodes: new Array(CLOUD_LIMITS.nodes + 1),
        edges: [],
      }),
    /renderer limit/,
  );
  assert.throws(
    () =>
      buildCloudGraph({
        nodes: [],
        edges: new Array(CLOUD_LIMITS.edges + 1),
      }),
    /renderer limit/,
  );
});

function layoutHarness(graph, extra = {}) {
  const workers = [],
    timers = new Map(),
    errors = [];
  let timerSequence = 0;
  const layout = createCloudLayout(graph, (error) => errors.push(error), {
    createWorker() {
      const worker = {
        messages: [],
        terminated: false,
        postMessage(message) {
          this.messages.push(message);
        },
        terminate() {
          this.terminated = true;
        },
      };
      workers.push(worker);
      return worker;
    },
    schedule(callback, delay) {
      const id = ++timerSequence;
      timers.set(id, { callback, delay });
      return id;
    },
    unschedule(id) {
      timers.delete(id);
    },
    ...extra,
  });
  return { layout, workers, timers, errors };
}

test("worker is bounded and terminated with timer release at its round limit", () => {
  const { layout, workers, timers, errors } = layoutHarness(
    buildCloudGraph(fixture()),
    { maxRounds: 2 },
  );
  layout.start();
  assert.equal(layout.isRunning(), true);
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].delay, 4000);
  const worker = workers[0];
  const nodes = worker.messages[0].nodes.slice(0);
  worker.onmessage({ data: { nodes } });
  assert.equal(worker.messages.length, 2);
  worker.onmessage({ data: { nodes } });
  assert.equal(worker.terminated, true);
  assert.equal(worker.onmessage, null);
  assert.equal(worker.onerror, null);
  assert.equal(layout.isRunning(), false);
  assert.equal(timers.size, 0);
  assert.deepEqual(errors, []);
});

test("stop rejects late worker results and a restarted worker owns a fresh generation", () => {
  const graph = buildCloudGraph(fixture());
  const { layout, workers, errors } = layoutHarness(graph);
  layout.start();
  const old = workers[0],
    late = old.onmessage;
  const before = graph.getNodeAttribute("a", "x");
  layout.stop();
  layout.start();
  assert.equal(old.terminated, true);
  const changed = new Float32Array(old.messages[0].nodes.slice(0));
  changed[0] = 999;
  late({ data: { nodes: changed.buffer } });
  assert.equal(graph.getNodeAttribute("a", "x"), before);
  assert.equal(layout.isRunning(), true);
  assert.equal(workers[1].terminated, false);
  layout.kill();
  assert.equal(workers[1].terminated, true);
  assert.deepEqual(errors, []);
});

test("malformed worker output stops safely and preserves the static graph", () => {
  const graph = buildCloudGraph(fixture());
  const { layout, workers, timers, errors } = layoutHarness(graph);
  layout.start();
  workers[0].onmessage({ data: { nodes: new Float32Array([NaN]).buffer } });
  assert.equal(layout.isRunning(), false);
  assert.equal(workers[0].terminated, true);
  assert.equal(timers.size, 0);
  assert.deepEqual(errors, ["layout"]);
  assert.ok(
    graph
      .nodes()
      .every((id) => Number.isFinite(graph.getNodeAttribute(id, "x"))),
  );
});

test("worker timeout, construction failure and isolated-only snapshots release resources", () => {
  const graph = buildCloudGraph(fixture());
  const bounded = layoutHarness(graph);
  bounded.layout.start();
  [...bounded.timers.values()][0].callback();
  assert.equal(bounded.layout.isRunning(), false);
  assert.equal(bounded.workers[0].terminated, true);
  assert.equal(bounded.timers.size, 0);
  const unavailable = layoutHarness(graph, {
    createWorker() {
      throw new Error("disabled");
    },
  });
  unavailable.layout.start();
  assert.deepEqual(unavailable.errors, ["layout"]);
  assert.equal(unavailable.layout.isRunning(), false);
  const isolated = layoutHarness(
    buildCloudGraph({ nodes: fixture().nodes, edges: [] }),
  );
  isolated.layout.start();
  assert.equal(isolated.workers.length, 0);
});

test("five thousand synthetic notes retain exact edge count and searchable identities", () => {
  const nodes = Array.from({ length: 5000 }, (_, i) => ({
    id: `note-${i}`,
    label: `合成笔记 ${i}`,
    group: `group-${i % 17}`,
  }));
  const edges = nodes.slice(1).map((node, i) => ({
    id: `edge-${i}`,
    source: nodes[Math.floor(i / 2)].id,
    target: node.id,
    kind: "explicit",
  }));
  const graph = buildCloudGraph({ nodes, edges });
  assert.equal(graph.order, 5000);
  assert.equal(graph.size, 4999);
  assert.equal(graph.getNodeAttribute("note-4999", "label"), "合成笔记 4999");
  assert.ok(
    graph
      .nodes()
      .every((id) => Number.isFinite(graph.getNodeAttribute(id, "y"))),
  );
});
