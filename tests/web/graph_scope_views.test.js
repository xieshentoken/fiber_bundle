import test from "node:test";
import assert from "node:assert/strict";
import {
  graphDOM,
  nextTurn,
  memoryStorage,
  apiFixture,
} from "./graph_scope_view_fixture.js";
import { GRAPH_SCOPE_KEY } from "../../src/fiber_bundle/web/graph_scope_preferences.js";
globalThis.localStorage = memoryStorage();
const { createKnowledgeGraphView } =
  await import("../../src/fiber_bundle/web/knowledge_graph.js");
const { createView } =
  await import("../../src/fiber_bundle/web/atlas/view_graph.js");
async function fixture(kind, alter = () => {}) {
  const dom = graphDOM(),
    { api, calls } = apiFixture();
  alter(api, calls);
  const forces = [];
  const view =
    kind === "classic"
      ? createKnowledgeGraphView(api)
      : createView({
          api,
          store: {
            clients: (options) => {
              forces.push(options);
              return api.clients();
            },
            vaults: (options) => {
              forces.push(options);
              return api.vaults();
            },
          },
          navigate() {},
        });
  if (view.node) document.body.append(view.node);
  const scopes = () =>
    kind === "classic"
      ? [
          document.getElementById("graph-client"),
          document.getElementById("graph-vault"),
        ]
      : Array.from(view.node.querySelectorAll("select")).slice(0, 2);
  const close = () => {
    if (view.dispose) view.dispose();
    else view.clear();
    dom.restore();
  };
  return {
    dom,
    api,
    calls,
    forces,
    view,
    scopes,
    close,
    host: () => view.node || document.getElementById("graph-panel"),
  };
}
for (const kind of ["classic", "atlas"]) {
  test(`${kind}: entry uses saved default, switches auto-read, page refresh keeps temporary scope, and clear/reentry reapplies saved default`, async () => {
    globalThis.localStorage = memoryStorage();
    globalThis.localStorage.setItem(
      GRAPH_SCOPE_KEY,
      JSON.stringify({ version: 1, client_id: "c", vault_id: "b" }),
    );
    const f = await fixture(kind);
    try {
      await f.view.refresh();
      await nextTurn();
      const [client, vault] = f.scopes();
      assert.equal(client.value, "c");
      assert.equal(vault.value, "b");
      assert(f.host().textContent.includes("note-b"));
      vault.value = "a";
      vault.listeners.change();
      await nextTurn();
      assert.equal(vault.value, "a");
      assert(
        f.calls.some(([name, value]) => name === "notes" && value === "a"),
      );
      assert(f.host().textContent.includes("note-a"));
      await f.view.refresh();
      assert.equal(vault.value, "a");
      assert.equal(
        JSON.parse(globalThis.localStorage.getItem(GRAPH_SCOPE_KEY)).vault_id,
        "b",
      );
      const beforeSave = f.calls.length;
      f.host().querySelector('[data-scope-default="save"]').listeners.click();
      assert.equal(f.calls.length, beforeSave);
      assert.equal(
        JSON.parse(globalThis.localStorage.getItem(GRAPH_SCOPE_KEY)).vault_id,
        "a",
      );
      if (kind === "classic") {
        f.view.clear();
        await f.view.refresh();
        assert.equal(vault.value, "a");
      }
      if (kind === "atlas")
        assert(f.forces.every((options) => options.force === true));
      f.host().querySelector('[data-scope-default="clear"]').listeners.click();
      assert.equal(globalThis.localStorage.getItem(GRAPH_SCOPE_KEY), null);
      assert.equal(vault.value, "a");
      assert(
        f.calls.every(([name]) =>
          [
            "clients",
            "vaults",
            "organization",
            "notes",
            "suggestions",
            "jobs",
            "mocs",
          ].includes(name),
        ),
      );
    } finally {
      f.close();
    }
  });
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const notes = (vault, suffix = "") => [
  {
    note_id: vault,
    title: `note-${vault}${suffix}`,
    note_path: vault + ".md",
    selectable: true,
    sha256: "sha",
  },
];
for (const kind of ["classic", "atlas"]) {
  test(`${kind}: A→B→A starts a new request and ignores old errors, results, and completion`, async () => {
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      await f.view.refresh();
      await nextTurn();
      const pending = [];
      f.api.graph.notes = async (_client, vault) => {
        const task = deferred();
        pending.push({ vault, ...task });
        return task.promise;
      };
      const first = f.view.refresh();
      await nextTurn();
      const duplicate = f.view.refresh();
      await nextTurn();
      assert.equal(pending.length, 1);
      const vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      await nextTurn();
      assert(!f.host().textContent.includes("note-a"));
      vault.value = "a";
      vault.listeners.change();
      await nextTurn();
      assert.deepEqual(
        pending.map((task) => task.vault),
        ["a", "b", "a"],
      );
      pending[0].reject(Error("obsolete A failure"));
      pending[1].resolve(notes("b", "-obsolete"));
      await nextTurn();
      await first;
      await duplicate;
      assert.doesNotMatch(
        document.body.textContent,
        /obsolete A failure|note-b-obsolete/,
      );
      assert.equal(
        f
          .host()
          .querySelector("[data-graph-scope-controls]")
          .getAttribute("aria-busy"),
        "true",
      );
      pending[2].resolve(notes("a", "-latest"));
      await nextTurn();
      assert(f.host().textContent.includes("note-a-latest"));
      assert.equal(
        f
          .host()
          .querySelector("[data-graph-scope-controls]")
          .getAttribute("aria-busy"),
        "false",
      );
    } finally {
      f.close();
    }
  });

  test(`${kind}: graph results render while organization is pending, then report partial catalog failure`, async () => {
    globalThis.localStorage = memoryStorage();
    const org = deferred();
    const f = await fixture(kind, (api) => {
      api.organization.list = () => org.promise;
    });
    try {
      let returned = false;
      const pending = f.view.refresh().then(() => {
        returned = true;
      });
      await nextTurn();
      assert(f.host().textContent.includes("note-a"));
      assert.equal(returned, false);
      assert.equal(
        f
          .host()
          .querySelector("[data-graph-scope-controls]")
          .getAttribute("aria-busy"),
        "true",
      );
      org.reject(Error("synthetic catalog unavailable"));
      await pending;
      await nextTurn();
      assert.match(
        f.host().textContent,
        /部分读取未完成.*synthetic catalog unavailable/,
      );
      assert(f.host().textContent.includes("note-a"));
      assert.equal(
        f
          .host()
          .querySelector("[data-graph-scope-controls]")
          .getAttribute("aria-busy"),
        "false",
      );
    } finally {
      f.close();
    }
  });

  test(`${kind}: current permission failures clear content but remain visible, and late page errors stay silent`, async () => {
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      await f.view.refresh();
      f.api.graph.notes = async () => {
        const error = Error("current permission revoked");
        error.status = 403;
        throw error;
      };
      await f.view.refresh();
      await nextTurn();
      assert(!f.host().textContent.includes("note-a"));
      assert.match(
        f.host().querySelector("[data-graph-scope-controls]").textContent,
        /读取未完成.*current permission revoked/,
      );
      assert.equal(
        f.host().querySelector('[data-scope-default="save"]').disabled,
        true,
      );
      const late = deferred();
      f.api.graph.notes = () => late.promise;
      const pending = f.view.refresh();
      await nextTurn();
      if (kind === "classic") f.view.clear();
      else f.view.dispose();
      late.reject(Error("after leaving this page"));
      await pending;
      await nextTurn();
      assert.doesNotMatch(document.body.textContent, /after leaving this page/);
    } finally {
      f.close();
    }
  });

  test(`${kind}: invalid defaults fall back safely and no authorized scope has no successful read state`, async () => {
    globalThis.localStorage = memoryStorage();
    globalThis.localStorage.setItem(
      GRAPH_SCOPE_KEY,
      JSON.stringify({ version: 1, client_id: "c", vault_id: "missing" }),
    );
    const f = await fixture(kind);
    try {
      await f.view.refresh();
      assert.equal(f.scopes()[1].value, "a");
      assert.match(f.host().textContent, /默认已不可用或无查询权限/);
      assert.equal(
        JSON.parse(globalThis.localStorage.getItem(GRAPH_SCOPE_KEY)).vault_id,
        "missing",
      );
      f.api.clients = async () => [
        { id: "no-grants", active: true, name: "No grants", grants: [] },
      ];
      const before = f.calls.filter(([name]) => name === "notes").length;
      await f.view.refresh();
      await nextTurn();
      assert.equal(f.calls.filter(([name]) => name === "notes").length, before);
      assert.match(f.host().textContent, /没有可查询知识库/);
      assert.doesNotMatch(f.host().textContent, /本次读取已返回|note-a|note-b/);
    } finally {
      f.close();
    }
  });

  test(`${kind}: unsaved new-MOC text can cancel a switch without reading another scope`, async () => {
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      await f.view.refresh();
      await nextTurn();
      const title = f.host().querySelector('[aria-label="新 MOC 名称"]');
      assert(title);
      title.value = "Keep this draft";
      title.listeners.input();
      window.confirm = () => false;
      const before = f.calls.length,
        [client, vault] = f.scopes();
      vault.value = "b";
      vault.listeners.change();
      await nextTurn();
      assert.equal(client.value, "c");
      assert.equal(vault.value, "a");
      assert.equal(f.calls.length, before);
      assert.equal(title.value, "Keep this draft");
    } finally {
      f.close();
    }
  });

  test(`${kind}: an in-flight maintenance preview blocks switching until its reply without duplicating submission`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    globalThis.localStorage = memoryStorage();
    const submission = deferred();
    let writes = 0;
    const f = await fixture(kind, (api) => {
      api.organization.maintain = async () => {
        writes++;
        return submission.promise;
      };
    });
    try {
      await f.view.refresh();
      await nextTurn();
      const submit = Array.from(f.host().querySelectorAll("button")).find(
        (node) => node.textContent === "生成整理预览",
      );
      assert(submit);
      const request = submit.listeners.click();
      await nextTurn();
      const before = f.calls.length,
        vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      await nextTurn();
      assert.equal(vault.value, "a");
      assert.equal(f.calls.length, before);
      assert.equal(writes, 1);
      submission.resolve({ id: "new-preview" });
      await request;
      await nextTurn();
      vault.value = "b";
      vault.listeners.change();
      await nextTurn();
      assert.equal(vault.value, "b");
      assert(
        f.calls.some(([name, value]) => name === "notes" && value === "b"),
      );
    } finally {
      f.close();
    }
  });
}

test("graph search state clears with a scope rather than persisting hidden old results", async () => {
  const { createGraphMap } =
    await import("../../src/fiber_bundle/web/atlas/graph_map.js");
  globalThis.localStorage = memoryStorage();
  const f = await fixture("classic");
  try {
    await f.view.refresh();
    document.getElementById("graph-map-search-text").value =
      "previous scope search";
    document.getElementById("graph-search-results").textContent =
      "previous scope result";
    const vault = f.scopes()[1];
    vault.value = "b";
    vault.listeners.change();
    await nextTurn();
    assert.equal(document.getElementById("graph-map-search-text").value, "");
    assert.equal(
      document.getElementById("graph-search-results").textContent,
      "",
    );
    const map = createGraphMap(f.api.graph, () => ({
      client_id: "c",
      vault_id: "a",
    }));
    const search = map.node.querySelector("input");
    search.value = "old query";
    map.node.children[1].textContent = "old graph search results";
    map.clear();
    assert.equal(search.value, "");
    assert.doesNotMatch(map.node.textContent, /old graph search results/);
  } finally {
    f.close();
  }
});

for (const kind of ["classic", "atlas"]) {
  test(`${kind}: real custom-select events restore committed values before the in-app dialog, and cancel/Escape preserve drafts`, async () => {
    const { browserInteractions } =
      await import("./graph_scope_view_fixture.js");
    const { enhanceSelects } =
      await import("../../src/fiber_bundle/web/select_controls.js");
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    let selects;
    try {
      browserInteractions();
      await f.view.refresh();
      await nextTurn();
      window.confirm = () => {
        throw Error("native scope confirmation must not run");
      };
      const [client, vault] = f.scopes(),
        title = f.host().querySelector('[aria-label="新 MOC 名称"]');
      title.value = "Retain this MOC";
      title.dispatchEvent(new window.Event("input", { bubbles: true }));
      client.setAttribute("aria-label", "test owner");
      vault.setAttribute("aria-label", "test vault");
      selects = enhanceSelects(f.host());
      const chooseB = () => {
        const trigger = vault.parentElement.querySelector('[role="combobox"]');
        trigger.listeners.click();
        const list = document.getElementById(
          trigger.getAttribute("aria-controls"),
        );
        list.querySelector('[data-index="1"]').listeners.click();
        return trigger;
      };
      for (const close of ["cancel", "escape"]) {
        const before = f.calls.length,
          trigger = chooseB();
        // All of these hold before waiting even one microtask.
        assert.equal(vault.value, "a");
        assert.match(trigger.textContent, /Alpha/);
        assert.equal(f.calls.length, before);
        const dialog = document.querySelector("dialog");
        assert(dialog?.open);
        assert.match(dialog.textContent, /放弃草稿并切换/);
        // A duplicate change cannot create a second confirmation or scope read.
        vault.value = "b";
        vault.dispatchEvent(new window.Event("change", { bubbles: true }));
        assert.equal(document.querySelectorAll("dialog").length, 1);
        assert.equal(vault.value, "a");
        f.host().querySelector('[data-scope-default="save"]').listeners.click();
        assert.equal(
          JSON.parse(globalThis.localStorage.getItem(GRAPH_SCOPE_KEY)).vault_id,
          "a",
        );
        assert.equal(f.calls.length, before);
        if (close === "cancel")
          dialog.querySelectorAll("button")[0].listeners.click();
        else
          dialog.dispatchEvent(
            new window.Event("cancel", { cancelable: true }),
          );
        await nextTurn();
        assert.equal(document.querySelector("dialog"), null);
        assert.equal(client.value, "c");
        assert.equal(vault.value, "a");
        assert.equal(f.calls.length, before);
        assert.equal(title.value, "Retain this MOC");
      }
      const before = f.calls.filter(
        ([name, value]) => name === "notes" && value === "b",
      ).length;
      chooseB();
      const dialog = document.querySelector("dialog");
      dialog.querySelectorAll("button")[1].listeners.click();
      await nextTurn();
      assert.equal(vault.value, "b");
      assert.equal(
        f.calls.filter(([name, value]) => name === "notes" && value === "b")
          .length,
        before + 1,
      );
      assert.notEqual(
        f.host().querySelector('[aria-label="新 MOC 名称"]')?.value,
        "Retain this MOC",
      );
    } finally {
      selects?.destroy();
      f.close();
    }
  });

  test(`${kind}: leaving with a real pending dialog aborts it and prevents a late accepted scope read`, async () => {
    const { browserInteractions } =
      await import("./graph_scope_view_fixture.js");
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      browserInteractions();
      await f.view.refresh();
      await nextTurn();
      const title = f.host().querySelector('[aria-label="新 MOC 名称"]');
      title.value = "Keep";
      title.listeners.input();
      const vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      const dialog = document.querySelector("dialog"),
        accept = dialog.querySelectorAll("button")[1];
      const before = f.calls.length;
      if (kind === "classic") f.view.clear();
      else f.view.dispose();
      assert.equal(document.querySelector("dialog"), null);
      accept.listeners.click();
      await nextTurn();
      assert.equal(f.calls.length, before);
      assert.equal(vault.value, "a");
    } finally {
      f.close();
    }
  });

  test(`${kind}: non-boolean native fallbacks do not authorize discarding a scope draft`, async () => {
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      await f.view.refresh();
      await nextTurn();
      const title = f.host().querySelector('[aria-label="新 MOC 名称"]');
      title.value = "Keep";
      title.listeners.input();
      const vault = f.scopes()[1],
        before = f.calls.length;
      for (const answer of [undefined, "true", 1, {}]) {
        window.confirm = () => answer;
        vault.value = "b";
        vault.listeners.change();
        await nextTurn();
        assert.equal(vault.value, "a");
        assert.equal(title.value, "Keep");
        assert.equal(f.calls.length, before);
      }
    } finally {
      f.close();
    }
  });
}

for (const kind of ["classic", "atlas"]) {
  test(`${kind}: a fresh permission catalog invalidates and closes pending consent without discarding the old draft`, async () => {
    const { browserInteractions } =
      await import("./graph_scope_view_fixture.js");
    const { clientList } = await import("./graph_scope_view_fixture.js");
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      browserInteractions();
      await f.view.refresh();
      await nextTurn();
      const catalog = deferred();
      f.api.clients = () => catalog.promise;
      const reading = f.view.refresh();
      await nextTurn();
      const title = f.host().querySelector('[aria-label="新 MOC 名称"]');
      title.value = "Permission overlap draft";
      title.listeners.input();
      const vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      const dialog = document.querySelector("dialog"),
        lateAccept = dialog.querySelectorAll("button")[1];
      window.confirm = () => {
        throw Error("metadata must not open a second native prompt");
      };
      catalog.resolve(
        clientList.map((client) => ({
          ...client,
          grants: client.grants.filter((grant) => grant.vault_id !== "b"),
        })),
      );
      await reading;
      await nextTurn();
      assert.equal(document.querySelector("dialog"), null);
      lateAccept.listeners.click();
      await nextTurn();
      assert.equal(vault.value, "a");
      assert.equal(title.value, "Permission overlap draft");
      assert(!Array.from(vault.options).some((option) => option.value === "b"));
      assert(
        !f.calls.some(([name, value]) => name === "notes" && value === "b"),
      );
    } finally {
      f.close();
    }
  });

  test(`${kind}: an existing old-scope read settles behind pending consent without native prompts or target requests`, async () => {
    const { browserInteractions } =
      await import("./graph_scope_view_fixture.js");
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      browserInteractions();
      await f.view.refresh();
      await nextTurn();
      const oldRead = deferred();
      f.api.graph.notes = async (_client, vault) => {
        f.calls.push(["held-notes", vault]);
        return oldRead.promise;
      };
      const reading = f.view.refresh();
      await nextTurn();
      const title = f.host().querySelector('[aria-label="新 MOC 名称"]');
      title.value = "Keep while reading";
      title.listeners.input();
      const vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      window.confirm = () => {
        throw Error("old read must not open native confirmation");
      };
      oldRead.resolve(notes("a", "-held"));
      await reading;
      await nextTurn();
      assert.equal(vault.value, "a");
      assert(document.querySelector("dialog")?.open);
      assert.deepEqual(
        f.calls.filter(([name]) => name === "held-notes"),
        [["held-notes", "a"]],
      );
      assert(
        !f.calls.some(
          ([name, value]) =>
            ["notes", "jobs", "organization", "suggestions", "mocs"].includes(
              name,
            ) && value === "b",
        ),
      );
      document
        .querySelector("dialog")
        .querySelectorAll("button")[0]
        .listeners.click();
      await nextTurn();
      assert.equal(title.value, "Keep while reading");
    } finally {
      f.close();
    }
  });
}

test("Atlas partial organization errors name their source, retain index controls, and avoid doubled punctuation or visibility promises", async () => {
  const { setLocale } =
    await import("../../src/fiber_bundle/web/i18n/index.js");
  for (const language of ["zh-CN", "en"]) {
    globalThis.localStorage = memoryStorage();
    setLocale(language);
    const f = await fixture("atlas", (api) => {
      api.organization.list = async () => {
        throw Error("synthetic organization unavailable。");
      };
    });
    try {
      await f.view.refresh();
      await nextTurn();
      const text = f.host().textContent;
      assert.match(
        text,
        language === "en"
          ? /organization catalog could not be read/
          : /整理目录读取未完成/,
      );
      assert.doesNotMatch(
        text,
        /笔记列表加载失败|Note list.*failed|。。|unavailable。\./i,
      );
      assert.doesNotMatch(text, /已返回内容保留|Returned content is preserved/);
      assert.match(
        text,
        language === "en" ? /Update this vault's index/ : /更新此知识库索引/,
      );
    } finally {
      f.close();
      setLocale("zh-CN");
    }
  }
});

for (const kind of ["classic", "atlas"]) {
  test(`${kind}: selecting another authorized vault recovers automatic reading after a current 403`, async () => {
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      await f.view.refresh();
      await nextTurn();
      f.api.graph.notes = async (_client, vault) => {
        f.calls.push(["recover-notes", vault]);
        if (vault === "a") {
          const error = Error("revoked current query");
          error.status = 403;
          throw error;
        }
        return notes(vault);
      };
      await f.view.refresh();
      await nextTurn();
      assert.doesNotMatch(f.host().textContent, /note-a/);
      const vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      await nextTurn();
      assert.equal(vault.value, "b");
      assert.match(f.host().textContent, /note-b/);
      assert.deepEqual(
        f.calls.filter(
          ([name, value]) => name === "recover-notes" && value === "b",
        ),
        [["recover-notes", "b"]],
      );
    } finally {
      f.close();
    }
  });

  test(`${kind}: a dialog component failure preserves drafts and reports one understandable confirmation error`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const { browserInteractions } =
      await import("./graph_scope_view_fixture.js");
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      browserInteractions();
      await f.view.refresh();
      await nextTurn();
      const title = f.host().querySelector('[aria-label="新 MOC 名称"]');
      title.value = "Keep despite dialog failure";
      title.listeners.input();
      window.Element.prototype.showModal = () => {
        throw Error("unavailable dialog surface");
      };
      const before = f.calls.length,
        vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      await nextTurn();
      assert.equal(vault.value, "a");
      assert.equal(title.value, "Keep despite dialog failure");
      assert.equal(f.calls.length, before);
      assert.equal(document.querySelector("dialog"), null);
      assert.equal(
        (document.body.textContent.match(/无法完成切换确认/g) || []).length,
        1,
      );
    } finally {
      f.close();
    }
  });
}

for (const kind of ["classic", "atlas"]) {
  test(`${kind}: clearing a revoked current scope during consent still allows an explicit authorized selection to recover`, async () => {
    const { browserInteractions, clientList } =
      await import("./graph_scope_view_fixture.js");
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind);
    try {
      browserInteractions();
      await f.view.refresh();
      await nextTurn();
      const catalog = deferred();
      f.api.clients = () => catalog.promise;
      const reading = f.view.refresh();
      await nextTurn();
      const title = f.host().querySelector('[aria-label="新 MOC 名称"]');
      title.value = "Before revocation";
      title.listeners.input();
      const vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      const dialog = document.querySelector("dialog"),
        lateAccept = dialog.querySelectorAll("button")[1];
      catalog.resolve(
        clientList.map((client) => ({
          ...client,
          grants: client.grants.filter((grant) => grant.vault_id === "b"),
        })),
      );
      await reading;
      await nextTurn();
      assert.equal(document.querySelector("dialog"), null);
      assert.equal(vault.value, "");
      lateAccept.listeners.click();
      await nextTurn();
      assert(
        !f.calls.some(([name, value]) => name === "notes" && value === "b"),
      );
      vault.value = "b";
      vault.listeners.change();
      await nextTurn();
      assert.equal(vault.value, "b");
      assert.match(f.host().textContent, /note-b/);
      assert.equal(
        f.calls.filter(([name, value]) => name === "notes" && value === "b")
          .length,
        1,
      );
    } finally {
      f.close();
    }
  });
}

for (const kind of ["classic", "atlas"]) {
  test(`${kind}: manual multi-MOC drafts survive page refresh without native confirmation and use scope-switch consent`, async () => {
    const { browserInteractions } =
      await import("./graph_scope_view_fixture.js");
    globalThis.localStorage = memoryStorage();
    const f = await fixture(kind, (api) => {
      const list = api.organization.list;
      api.organization.list = async (scope) => ({
        ...(await list(scope)),
        items: [
          {
            id: "manual-draft",
            title: "Manual Draft",
            path: "Book/Draft",
            kind: "expert",
            status: "new",
            moc_ids: [],
          },
        ],
        mocs: [
          { id: "one", title: "One MOC" },
          { id: "two", title: "Two MOC" },
        ],
        total: 1,
      });
    });
    try {
      browserInteractions();
      await f.view.refresh();
      await nextTurn();
      window.confirm = () => {
        throw Error(
          "same-scope manual refresh must not ask native confirmation",
        );
      };
      const controls = () =>
        f.host().querySelector('[data-manual-entity="manual-draft"]');
      const checked = () =>
        controls()
          .querySelectorAll("input")
          .filter((node) => node.type === "checkbox");
      for (const input of checked()) {
        input.checked = true;
        input.listeners.change();
      }
      await f.view.refresh();
      await nextTurn();
      assert.deepEqual(
        checked().map((node) => node.checked),
        [true, true],
      );
      const vault = f.scopes()[1];
      vault.value = "b";
      vault.listeners.change();
      assert.equal(vault.value, "a");
      let dialog = document.querySelector("dialog");
      assert(dialog?.open);
      dialog.querySelectorAll("button")[0].listeners.click();
      await nextTurn();
      assert.equal(vault.value, "a");
      assert.deepEqual(
        checked().map((node) => node.checked),
        [true, true],
      );
      vault.value = "b";
      vault.listeners.change();
      dialog = document.querySelector("dialog");
      dialog.querySelectorAll("button")[1].listeners.click();
      await nextTurn();
      assert.equal(vault.value, "b");
      assert.deepEqual(
        checked().map((node) => node.checked),
        [false, false],
      );
    } finally {
      f.close();
    }
  });
}

test("Atlas top page-header refresh preserves manual drafts while the leave guard still protects them", async () => {
  const { pageHeader } =
    await import("../../src/fiber_bundle/web/atlas/shell.js");
  const { pageRefreshAction } =
    await import("../../src/fiber_bundle/web/atlas/page_refresh.js");
  const { createDraftGuard } =
    await import("../../src/fiber_bundle/web/unsaved_changes.js");
  globalThis.localStorage = memoryStorage();
  const f = await fixture("atlas", (api) => {
    const list = api.organization.list;
    api.organization.list = async (scope) => ({
      ...(await list(scope)),
      items: [
        {
          id: "manual-draft",
          title: "Manual Draft",
          path: "Book/Draft",
          kind: "expert",
          status: "new",
          moc_ids: [],
        },
      ],
      mocs: [
        { id: "one", title: "One MOC" },
        { id: "two", title: "Two MOC" },
      ],
      total: 1,
    });
  });
  let header;
  try {
    await f.view.refresh();
    await nextTurn();
    const guard = createDraftGuard(document.body, {
      extraChanges: (_panel, purpose) =>
        f.view.dirty({ refresh: purpose === "refresh" }),
      signedIn: () => true,
    });
    header = pageHeader("graph", {
      onRefresh: pageRefreshAction(guard, () => f.view.refresh()),
    });
    document.body.append(header.node);
    const boxes = () =>
      f
        .host()
        .querySelector('[data-manual-entity="manual-draft"]')
        .querySelectorAll("input")
        .filter((node) => node.type === "checkbox");
    for (const box of boxes()) {
      box.checked = true;
      box.listeners.change();
    }
    window.confirm = () => {
      throw Error(
        "header refresh must preserve manual drafts without confirmation",
      );
    };
    await header.refreshButton.listeners.click();
    await nextTurn();
    assert.deepEqual(
      boxes().map((node) => node.checked),
      [true, true],
    );
    let prompts = 0;
    window.confirm = () => {
      prompts++;
      return false;
    };
    assert.equal(guard.confirmLeave(), false);
    assert.equal(prompts, 1);
    assert.deepEqual(
      boxes().map((node) => node.checked),
      [true, true],
    );
    const title = f.host().querySelector('[aria-label="新 MOC 名称"]');
    title.value = "Unsaved unrelated MOC";
    title.listeners.input();
    const before = f.calls.length;
    await header.refreshButton.listeners.click();
    assert.equal(prompts, 2);
    assert.equal(f.calls.length, before);
    assert.equal(title.value, "Unsaved unrelated MOC");
  } finally {
    header?.dispose();
    f.close();
  }
});
