import test from "node:test";
import assert from "node:assert/strict";
import {
  graphDOM,
  nextTurn,
  memoryStorage,
  apiFixture,
} from "./graph_scope_view_fixture.js";
import { GRAPH_SCOPE_KEY } from "../../src/fiber_bundle/web/graph_scope_preferences.js";
import {
  noteCloudSnapshot,
  noteCloudSearch,
} from "../../src/fiber_bundle/web/note_cloud_model.js";
import { createNoteCloudView } from "../../src/fiber_bundle/web/note_cloud.js";
import { createGraphApi } from "../../src/fiber_bundle/web/knowledge_graph_api.js";
import { parseRoute } from "../../src/fiber_bundle/web/atlas/nav.js";

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
function shapeNode(node) {
  return {
    aliases: [],
    availability: node.kind === "unresolved" ? "missing" : "current",
    historical: node.kind === "unresolved",
    metadata_visible: true,
    entity_id: node.id,
    note_id: null,
    sha256: node.kind === "unresolved" ? null : "a".repeat(64),
    ...node,
  };
}
function snapshot(vault = "a", nodes = null) {
  return {
    schema: "fiber-note-cloud/v2",
    snapshot_token: "token-" + vault,
    client_id: "c",
    vault_id: vault,
    nodes: (
      nodes || [
        {
          id: vault + "1",
          label: "长标题：<script>知识不是 HTML</script>",
          path: "原文/alpha.md",
          kind: "note",
          note_id: null,
        },
        {
          id: vault + "2",
          label: "Beta",
          path: "beta.md",
          kind: "note",
          note_id: "note:stable",
        },
        {
          id: vault + "3",
          label: "孤立",
          path: "isolated.md",
          kind: "note",
          note_id: null,
        },
      ]
    ).map(shapeNode),
    edges: nodes
      ? []
      : [
          {
            id: "edge",
            source: vault + "1",
            target: vault + "2",
            kind: "explicit",
          },
        ],
    coverage: {
      complete: true,
      links_complete: true,
      indexed_notes: nodes?.length || 3,
      current_notes:
        nodes?.filter((n) => ["note", "moc"].includes(n.kind)).length ?? 3,
      current_experts: nodes?.filter((n) => n.kind === "expert").length ?? 0,
      current_entities:
        nodes?.filter((n) => n.kind !== "unresolved").length ?? 3,
      unresolved_targets:
        nodes?.filter((n) => n.kind === "unresolved").length ?? 0,
      has_unresolved: nodes?.some((n) => n.kind === "unresolved") ?? false,
      targets_resolved: !(nodes?.some((n) => n.kind === "unresolved") ?? false),
      membership_edges: 0,
      manual_memberships: 0,
      system_memberships: 0,
      manual_locked_memberships: 0,
      sync_indexed_present: 0,
      sync_not_synced: 0,
      sync_unknown: 0,
      index_state: "ready",
      omitted_nodes: 0,
      omitted_edges: 0,
    },
  };
}
function fixture({ rendererFails = false, loadRenderer = null } = {}) {
  const dom = graphDOM();
  const documentEvents = new Map();
  document.addEventListener = (name, listener) =>
    documentEvents.set(name, listener);
  document.removeEventListener = (name, listener) => {
    if (documentEvents.get(name) === listener) documentEvents.delete(name);
  };
  globalThis.localStorage = memoryStorage();
  const { api } = apiFixture(),
    calls = [],
    instances = [];
  api.graph.noteCloud = async (_client, vault, focus) => {
    calls.push(["snapshot", vault, focus]);
    return snapshot(vault);
  };
  api.graph.noteCloudSearch = async (_client, vault) => ({
    schema: "fiber-note-cloud/v2",
    snapshot_token: "token-" + vault,
    client_id: "c",
    vault_id: vault,
    items: [],
    matched_count: 0,
  });
  const library = {
    createNoteCloudRenderer(options) {
      if (rendererFails) throw Error("no WebGL");
      const instance = {
        options,
        killed: false,
        value: null,
        setData(value) {
          this.value = value;
        },
        focusNode(id) {
          options.onSelect(id);
          return true;
        },
        clearSelection() {
          options.onSelect(null);
        },
        fit() {},
        zoomIn() {},
        zoomOut() {},
        kill() {
          this.killed = true;
        },
      };
      instances.push(instance);
      return instance;
    },
  };
  const view = createNoteCloudView(api, {
    loadRenderer: loadRenderer || (async () => library),
  });
  document.body.append(view.node);
  const byText = (text, host = view.node) =>
    [...host.querySelectorAll("button")].find(
      (node) =>
        node.textContent === text || node.textContent.endsWith(" · " + text),
    );
  return {
    api,
    calls,
    dom,
    documentEvents,
    view,
    instances,
    library,
    byText,
    scope: () => view.node.querySelectorAll("select").slice(0, 2),
    close() {
      view.dispose();
      dom.restore();
      delete globalThis.localStorage;
    },
  };
}

test("snapshot admits only exact scope, unique nodes, and complete endpoint edges", () => {
  assert.equal(
    noteCloudSnapshot(snapshot(), { client_id: "c", vault_id: "a" })
      .adjacency.get("a1")
      .get("a2")
      .some((edge) => edge.kind === "explicit"),
    true,
  );
  assert.throws(
    () => noteCloudSnapshot(snapshot("b"), { client_id: "c", vault_id: "a" }),
    /scope/,
  );
  const bad = snapshot();
  bad.edges[0].target = "hidden";
  assert.throws(
    () => noteCloudSnapshot(bad, { client_id: "c", vault_id: "a" }),
    /edge/,
  );
  assert.throws(
    () =>
      noteCloudSearch(
        { client_id: "c", vault_id: "b", items: [] },
        { client_id: "c", vault_id: "a" },
      ),
    /scope/,
  );
});

test("shared page presents actual nodes, full safe tooltip, selected neighbors and Esc cleanup", async () => {
  const f = fixture();
  try {
    await f.view.refresh();
    const renderer = f.instances[0],
      tooltip = f.view.node.querySelector(".nc-tooltip");
    assert.equal(renderer.value.nodes.length, 3);
    renderer.options.onHover({
      id: "a1",
      label: renderer.value.nodes[0].label,
      x: 400,
      y: 200,
    });
    assert.equal(tooltip.hidden, false);
    assert(tooltip.textContent.endsWith(renderer.value.nodes[0].label));
    assert.equal(tooltip.querySelector("script"), null);
    renderer.options.onSelect("a1");
    assert.match(f.view.node.querySelector(".nc-detail").textContent, /Beta/);
    assert.match(
      f.view.node.querySelector(".nc-detail").textContent,
      /未登记稳定身份/,
    );
    // Replacing a focused neighbor button can leave browser focus on body.
    document.activeElement = document.body;
    f.documentEvents.get("keydown")({ key: "Escape" });
    assert.equal(tooltip.hidden, true);
    assert.match(
      f.view.node.querySelector(".nc-detail").textContent,
      /选择一篇/,
    );
    assert.equal(f.view.node.querySelector(".nc-feedback").textContent, "");
  } finally {
    f.close();
  }
});

test("WebGL failure retains a bounded keyboard list and selectable isolated notes", async () => {
  const f = fixture({ rendererFails: true });
  try {
    const nodes = Array.from({ length: 5000 }, (_, i) => ({
      id: "id" + i,
      label: "笔记" + i,
      path: i + ".md",
      kind: "note",
      note_id: null,
    }));
    f.api.graph.noteCloud = async () => snapshot("a", nodes);
    await f.view.refresh();
    assert.equal(f.view.node.querySelector(".nc-list").open, true);
    assert.equal(
      f.view.node.querySelector(".nc-list-items").querySelectorAll("button")
        .length,
      40,
    );
    f.byText("笔记0").click();
    await nextTurn();
    assert.match(
      f.view.node.querySelector(".nc-detail").textContent,
      /没有直接关联/,
    );
    f.byText("下一页").click();
    await nextTurn();
    assert(
      f.view.node
        .querySelector(".nc-list-items")
        .querySelectorAll("button")[0]
        .textContent.endsWith("笔记40"),
    );
  } finally {
    f.close();
  }
});

test("scope switches discard late snapshots and reentry uses the shared saved default", async () => {
  const f = fixture(),
    delayed = deferred();
  try {
    globalThis.localStorage.setItem(
      GRAPH_SCOPE_KEY,
      JSON.stringify({ version: 1, client_id: "c", vault_id: "a" }),
    );
    f.api.graph.noteCloud = async (_client, vault) =>
      vault === "a" ? delayed.promise : snapshot("b");
    const old = f.view.refresh();
    await nextTurn();
    const [, vault] = f.scope();
    vault.value = "b";
    vault.listeners.change();
    await nextTurn();
    assert.equal(f.instances.at(-1).value.vault_id, "b");
    delayed.resolve(snapshot("a"));
    await old;
    assert.equal(f.instances.length, 1);
    assert.equal(f.instances[0].value.vault_id, "b");
    f.view.clear();
    f.api.graph.noteCloud = async (_client, value) => snapshot(value);
    await f.view.refresh();
    assert.equal(f.scope()[1].value, "a");
    assert.equal(f.instances[0].killed, true);
  } finally {
    f.close();
  }
});

test("search locates an indexed note outside the bounded view and rejects late old-scope matches", async () => {
  const f = fixture(),
    delayed = deferred();
  try {
    await f.view.refresh();
    f.api.graph.noteCloudSearch = async (_client, vault) => ({
      schema: "fiber-note-cloud/v2",
      snapshot_token: "token-" + vault,
      client_id: "c",
      vault_id: vault,
      items: [
        shapeNode({
          id: "outside",
          label: "未显示的笔记",
          path: "outside.md",
          kind: "note",
        }),
      ],
      matched_count: 1,
    });
    f.api.graph.noteCloud = async (_client, vault, focus) => {
      f.calls.push(["snapshot", vault, focus]);
      return snapshot(vault, [
        { id: focus, label: "未显示的笔记", path: "outside.md", kind: "note" },
      ]);
    };
    const query = f.view.node.querySelector("input");
    query.value = "outside";
    f.byText("查找").click();
    await nextTurn();
    f.byText("未显示的笔记 · outside.md").click();
    await nextTurn();
    assert.deepEqual(f.calls.at(-1), ["snapshot", "a", "outside"]);
    assert.match(
      f.view.node.querySelector(".nc-detail").textContent,
      /未显示的笔记/,
    );
    f.api.graph.noteCloudSearch = () => delayed.promise;
    query.value = "old";
    f.byText("查找").click();
    await nextTurn();
    const [, vault] = f.scope();
    vault.value = "b";
    vault.listeners.change();
    await nextTurn();
    delayed.resolve({
      client_id: "c",
      vault_id: "a",
      items: [{ id: "secret", label: "OLD SECRET", path: "secret.md" }],
    });
    await nextTurn();
    assert(!f.view.node.textContent.includes("OLD SECRET"));
  } finally {
    f.close();
  }
});

test("clearing the page invalidates a delayed renderer import and late permission failures", async () => {
  const delayed = deferred(),
    f = fixture({ loadRenderer: () => delayed.promise });
  try {
    const ready = f.view.refresh();
    await nextTurn();
    f.view.clear();
    delayed.resolve(f.library);
    await ready;
    assert.equal(f.instances.length, 0);
    assert.equal(
      f.view.node.querySelector(".nc-list-items").children.length,
      0,
    );
    assert.equal(f.view.node.getAttribute("aria-busy"), null);
  } finally {
    f.close();
  }
});

test("permission-denied search clears prior graph data, and detached native buttons do not open old scopes", async () => {
  const f = fixture();
  try {
    let opened = 0;
    window.fiberDesktop = {
      openNote: async () => {
        opened++;
      },
    };
    await f.view.refresh();
    f.instances[0].options.onSelect("a2");
    const oldOpen = f.byText("打开原笔记");
    assert(oldOpen);
    const [, vault] = f.scope();
    vault.value = "b";
    vault.listeners.change();
    await nextTurn();
    oldOpen.click();
    await nextTurn();
    assert.equal(opened, 0);
    f.api.graph.noteCloudSearch = async () => {
      throw Object.assign(Error("denied"), { status: 403 });
    };
    f.view.node.querySelector("input").value = "test";
    f.byText("查找").click();
    await nextTurn();
    assert.equal(f.instances.at(-1).killed, true);
    assert.equal(
      f.view.node.querySelector(".nc-list-items").children.length,
      0,
    );
  } finally {
    delete window.fiberDesktop;
    f.close();
  }
});

test("transport encodes fixed read-only endpoints and Atlas admits only registered cloud routes", async () => {
  const calls = [],
    graph = createGraphApi(async (...args) => calls.push(args));
  await graph.noteCloud("c", "a", "index:hello/world");
  await graph.noteCloudSearch("c", "a", "title & path");
  assert.equal(
    calls[0][0],
    "/admin/knowledge-graph/note-cloud?client_id=c&vault_id=a&focus=index%3Ahello%2Fworld",
  );
  assert.match(calls[1][0], /query=title%20%26%20path$/);
  assert.equal(calls[0].length, 1);
  assert.equal(parseRoute("#/note-cloud").page, "note-cloud");
  assert.equal(parseRoute("#/unregistered-page").page, "home");
});

function mixedSnapshot() {
  const value = snapshot("a", [
    { id: "moc", kind: "moc", label: "材料 MOC", path: "Map/materials.md" },
    { id: "ordinary", kind: "note", label: "现存普通笔记", path: "current.md" },
    {
      id: "expert:one",
      kind: "expert",
      label: "Expert Draft",
      path: "Book/expert",
      version: "v1",
      entry_kind: "directory",
      artifact_stage: "draft",
      review_state: "deferred",
      knowledge_verified: false,
    },
    {
      id: "missing:one",
      kind: "unresolved",
      label: "历史笔记",
      path: "old/missing.md",
      reason: "identity_not_indexed",
    },
    {
      id: "missing:private",
      kind: "unresolved",
      label: "",
      path: "",
      reason: "target_unavailable",
      metadata_visible: false,
      entity_id: null,
    },
  ]);
  value.edges = [
    { id: "explicit", source: "moc", target: "ordinary", kind: "explicit" },
    ...[
      ["expert:one", ["manual"], true, "not_synced"],
      ["missing:one", ["manual"], false, "unknown"],
      ["missing:private", ["system"], false, "indexed_present"],
    ].map(([target, owners, locked, sync], i) => ({
      id: "membership-" + i,
      source: "moc",
      target,
      kind: "moc",
      membership: {
        owners,
        manual_locked: locked,
        file_sync: sync,
        evidence: "synthetic_test",
      },
    })),
  ];
  Object.assign(value.coverage, {
    membership_edges: 3,
    manual_memberships: 2,
    system_memberships: 1,
    manual_locked_memberships: 1,
    sync_not_synced: 1,
    sync_unknown: 1,
    sync_indexed_present: 1,
  });
  return value;
}

test("v2 validates expert aliases, unresolved targets and saved-membership metadata without upgrading explicit links", () => {
  const value = mixedSnapshot(),
    scope = { client_id: "c", vault_id: "a" };
  const admitted = noteCloudSnapshot(value, scope);
  assert.equal(admitted.byId.get("expert:one").entry_kind, "directory");
  assert.equal(
    admitted.adjacency.get("moc").get("ordinary")[0].membership,
    undefined,
  );
  const invalid = structuredClone(value);
  invalid.edges[1].membership.manual_locked = "true";
  assert.throws(() => noteCloudSnapshot(invalid, scope), /edge/);
  const duplicate = structuredClone(value);
  duplicate.nodes[2].aliases = ["ordinary"];
  assert.throws(() => noteCloudSnapshot(duplicate, scope), /alias/);
  const rawNoteClaim = structuredClone(value);
  rawNoteClaim.nodes[3].availability = "current";
  assert.throws(() => noteCloudSnapshot(rawNoteClaim, scope), /node/);
});

test("mixed entities remain auditable in the WebGL fallback, with historical targets outside current-note counts", async () => {
  const f = fixture({ rendererFails: true });
  try {
    f.api.graph.noteCloud = async () => mixedSnapshot();
    await f.view.refresh();
    const line = f.view.node.querySelector(".nc-status").textContent;
    assert.match(line, /现存笔记 2/);
    assert.match(line, /专家入口 1/);
    assert.match(line, /待核对目标 2/);
    f.byText("材料 MOC").click();
    await nextTurn();
    const relations = f.view.node.querySelector(".nc-detail");
    assert.match(relations.textContent, /人工锁定/);
    assert.match(relations.textContent, /未锁定/);
    assert.match(relations.textContent, /索引中有同步证据/);
    assert.match(relations.textContent, /尚未同步到 MOC 文件/);
    assert.match(relations.textContent, /文件同步状态待核对/);
    f.byText("Expert Draft").click();
    await nextTurn();
    assert.match(relations.textContent, /专家包入口/);
    assert.match(relations.textContent, /知识尚未核验/);
    assert.match(relations.textContent, /待审阅/);
    assert(!relations.textContent.includes("deferred"));
    f.byText("历史笔记", f.view.node.querySelector(".nc-list-items")).click();
    await nextTurn();
    assert.match(relations.textContent, /历史登记路径：old\/missing.md/);
    assert.match(relations.textContent, /当前索引未找到此身份/);
    assert.match(relations.textContent, /不是已核对的现存笔记/);
    assert.equal(f.byText("打开原笔记", relations), undefined);
    f.byText(
      "当前不可用目标",
      f.view.node.querySelector(".nc-list-items"),
    ).click();
    await nextTurn();
    assert(!relations.textContent.includes("old/missing.md"));
    assert(
      !f.view.node
        .querySelector(".nc-coverage")
        .textContent.includes("当前视图未完整"),
    );
  } finally {
    f.close();
  }
});

test("search with newer evidence refreshes a same-ID node before showing its relationships", async () => {
  const f = fixture();
  try {
    await f.view.refresh();
    const newer = snapshot();
    newer.snapshot_token = "new-evidence";
    newer.nodes[0].label = "最新标题与归属";
    f.api.graph.noteCloudSearch = async () => ({
      schema: "fiber-note-cloud/v2",
      client_id: "c",
      vault_id: "a",
      snapshot_token: "new-evidence",
      items: [newer.nodes[0]],
      matched_count: 1,
    });
    f.api.graph.noteCloud = async (_client, vault, focus) => {
      f.calls.push(["snapshot", vault, focus]);
      return newer;
    };
    f.view.node.querySelector("input").value = "最新";
    f.byText("查找").click();
    await nextTurn();
    f.byText("最新标题与归属 · 原文/alpha.md").click();
    await nextTurn();
    assert.deepEqual(f.calls.at(-1), ["snapshot", "a", "a1"]);
    assert.match(
      f.view.node.querySelector(".nc-detail").textContent,
      /最新标题与归属/,
    );
    assert.equal(f.instances[0].killed, true);
  } finally {
    f.close();
  }
});

test("confirmed missing publication stays historical and searchable without becoming a current note", async () => {
  const f = fixture({ rendererFails: true });
  try {
    const value = mixedSnapshot();
    value.nodes.find((node) => node.id === "missing:one").reason =
      "publication_missing_confirmed";
    f.api.graph.noteCloud = async () => value;
    await f.view.refresh();
    f.byText("历史笔记", f.view.node.querySelector(".nc-list-items")).click();
    await nextTurn();
    const detail = f.view.node.querySelector(".nc-detail");
    assert.match(detail.textContent, /原发布路径已标记缺失（历史记录）/);
    assert.match(detail.textContent, /历史登记路径：old\/missing.md/);
    assert.match(
      f.view.node.querySelector(".nc-status").textContent,
      /现存笔记 2/,
    );
    assert.match(
      f.view.node.querySelector(".nc-status").textContent,
      /待核对目标 2/,
    );
    assert.equal(f.byText("打开原笔记", detail), undefined);
  } finally {
    f.close();
  }
});
