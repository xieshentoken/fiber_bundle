import test from "node:test";
import assert from "node:assert/strict";
import { renderVaults } from "../../src/fiber_bundle/web/vaults.js";
import { clearNoticeTimer } from "../../src/fiber_bundle/web/notice_lifetime.js";

function element(tagName, text = "") {
  return {
    tagName,
    textContent: text,
    children: [],
    dataset: {},
    classList: { add() {} },
    append(...nodes) {
      this.children.push(...nodes);
    },
    replaceChildren(...nodes) {
      this.children = nodes;
    },
    setAttribute() {},
    removeAttribute() {},
    addEventListener(name, work) {
      this[name] = work;
    },
  };
}
function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}
function fixture() {
  const previous = globalThis.document;
  const list = element("div"),
    notice = element("p");
  notice.dataset.page = "vaults";
  globalThis.document = {
    getElementById: (id) => (id === "vault-list" ? list : notice),
    createElement: element,
    createTextNode: (text) => element("#text", text),
  };
  return {
    list,
    notice,
    titles: () =>
      descendants(list)
        .filter((node) => node.tagName === "h3")
        .map((node) => node.textContent),
    button: (text) =>
      descendants(list).find(
        (node) => node.tagName === "button" && node.textContent === text,
      ),
    close() {
      clearNoticeTimer(notice);
      globalThis.document = previous;
    },
  };
}
function vault(name, state) {
  return {
    id: name,
    name,
    attachment_state: state,
    status: state === "attached" ? "active" : state,
  };
}

test("vault list hides completed Trash records and retains actionable states", () => {
  const f = fixture();
  try {
    const records = [
      vault("deleted", "trashed"),
      vault("detached", "detached"),
      vault("uncertain", "reconcile"),
      vault("pending", "trash_pending"),
      vault("connected", "attached"),
    ];
    const before = globalThis.structuredClone(records);
    renderVaults(records, {});
    assert.deepEqual(f.titles(), [
      "detached",
      "uncertain",
      "pending",
      "connected",
    ]);
    assert.ok(f.button("重新接入并重建索引"));
    assert.ok(f.button("核对系统恢复结果"));
    assert.deepEqual(records, before, "rendering preserves API audit records");
  } finally {
    f.close();
  }
});

test("an all-trashed list shows its empty state without reconnect controls", () => {
  const f = fixture();
  try {
    renderVaults([vault("deleted", "trashed"), { status: "trashed" }], {});
    assert.deepEqual(f.titles(), []);
    assert.match(f.list.children[0].textContent, /暂无启用的知识库/);
    assert.equal(f.button("重新接入并重建索引"), undefined);
  } finally {
    f.close();
  }
});

test("Trash cancellation does not submit; success removes its row and failure keeps it", async () => {
  for (const [accepted, fail] of [
    [false, false],
    [true, false],
    [true, true],
  ]) {
    const f = fixture(),
      previousWindow = globalThis.window;
    let previews = 0,
      refreshes = 0,
      confirmations = 0;
    globalThis.window = {
      confirm: () => {
        assert.equal(previews, 1);
        assert.equal(refreshes, 0);
        assert.equal(confirmations, 0);
        return accepted;
      },
    };
    try {
      const record = vault("synthetic", "attached");
      const api = {
        vaultTrashPreview: async (id) => {
          previews++;
          return { id, root: "/synthetic-vault", files: 0, bytes: 0 };
        },
        vaultTrashConfirm: async (id, root) => {
          confirmations++;
          assert.equal(id, record.id);
          assert.equal(root, "/synthetic-vault");
          if (fail) throw new Error("synthetic Trash rejection");
          record.attachment_state = record.status = "trashed";
        },
      };
      const refresh = async () => {
        refreshes++;
        renderVaults([record], api, refresh);
      };
      renderVaults([record], api, refresh);
      await f.button("移到废纸篓").click();
      assert.equal(confirmations, accepted ? 1 : 0);
      assert.equal(refreshes, accepted && !fail ? 1 : 0);
      assert.deepEqual(f.titles(), accepted && !fail ? [] : ["synthetic"]);
      if (!accepted) assert.equal(f.notice.textContent, "");
      else
        assert.match(
          f.notice.textContent,
          fail ? /synthetic Trash rejection/ : /已移到系统废纸篓/,
        );
    } finally {
      f.close();
      globalThis.window = previousWindow;
    }
  }
});
