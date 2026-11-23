import test from "node:test";
import assert from "node:assert/strict";
import { installDOM } from "./reliability_dom_fixture.js";
import { createMemberSession } from "../../src/fiber_bundle/web/organization_member_session.js";
import { createOrganizationView } from "../../src/fiber_bundle/web/organization.js";
import { setLocale } from "../../src/fiber_bundle/web/i18n/index.js";

const mocs = [
  { id: "engineering", title: "工程 MOC" },
  { id: "materials", title: "材料 MOC" },
];
const item = {
  id: "draft",
  title: "Synthetic Draft",
  kind: "expert",
  path: "Book/Draft",
  moc_ids: [],
  status: "new",
};
const catalog = () => ({
  items: [{ ...item }],
  mocs,
  jobs: [],
  total: 1,
  next_offset: null,
  policy: {
    revision: 1,
    settings: {
      mode: "off",
      include: [],
      exclude: [],
      conditions: [],
      rules: [],
    },
  },
});
const completed = () => ({
  results: mocs.map((moc) => ({ moc_id: moc.id, completed: true })),
});
const button = (host, label) =>
  host.querySelectorAll("button").find((node) => node.textContent === label);
const picker = (host) => host.querySelector('[data-manual-entity="draft"]');
const boxes = (host) =>
  picker(host)
    .querySelectorAll("input")
    .filter((node) => node.type === "checkbox");
function choose(host, index, checked = true) {
  const box = boxes(host)[index];
  box.checked = checked;
  box.listeners.change();
}
async function fixture() {
  const dom = installDOM();
  setLocale("zh-CN");
  const notice = document.createElement("p");
  notice.id = "notice";
  document.body.append(notice);
  let fixed = { client_id: "c", vault_id: "a" },
    data = catalog(),
    failure = null;
  const calls = [];
  const api = {
    list: async (...args) => {
      calls.push(["list", ...args]);
      if (failure) throw failure;
      return data;
    },
    progress: async () => ({ jobs: [] }),
    members: async (body) => {
      calls.push(["members", body]);
      return completed();
    },
  };
  const view = createOrganizationView(api, () => fixed);
  document.body.append(view.host);
  await view.refresh();
  return {
    ...dom,
    api,
    view,
    calls,
    scope: (value) => {
      fixed = value;
    },
    data: (value) => {
      data = value;
    },
    fail: (value) => {
      failure = value;
    },
    close() {
      view.clear();
      dom.restore();
    },
  };
}

test("same-scope refresh and progress repaint retain selected MOCs, filter and expansion", async () => {
  const f = await fixture();
  try {
    choose(f.view.host, 0);
    choose(f.view.host, 1);
    const details = picker(f.view.host).querySelector("details");
    details.open = true;
    details.listeners.toggle();
    const search = picker(f.view.host).querySelector("input");
    search.value = "材料";
    search.listeners.input();
    assert.equal(f.view.dirty(), true);
    assert.equal(f.view.dirty({ manual: false }), false);
    await f.view.refresh();
    assert.notEqual(picker(f.view.host).querySelector("details"), details);
    assert.deepEqual(
      boxes(f.view.host).map((box) => box.checked),
      [true, true],
    );
    assert.equal(picker(f.view.host).querySelector("details").open, true);
    assert.equal(picker(f.view.host).querySelector("input").value, "材料");
    await f.view.refresh({ automatic: true, force: true });
    assert.deepEqual(
      boxes(f.view.host).map((box) => box.checked),
      [true, true],
    );
    assert.equal(picker(f.view.host).querySelector("input").value, "材料");
    assert.equal(picker(f.view.host).querySelector("details").open, true);
    await button(f.view.host, "查看全部知识").listeners.click();
    assert.deepEqual(
      boxes(f.view.host).map((box) => box.checked),
      [true, true],
    );
    assert.equal(picker(f.view.host).querySelector("input").value, "材料");
  } finally {
    f.close();
  }
});

test("one frozen batch disables all related choices and duplicate actions until its receipt", async () => {
  const f = await fixture();
  let resolve;
  f.api.members = (body) => {
    f.calls.push(["members", body]);
    return new Promise((done) => {
      resolve = done;
    });
  };
  try {
    choose(f.view.host, 0);
    choose(f.view.host, 1);
    const controls = picker(f.view.host),
      action = button(controls, "人工增挂并锁定");
    const pending = action.listeners.click();
    assert.equal(f.view.busy(), true);
    assert(controls.querySelectorAll("input").every((node) => node.disabled));
    assert(controls.querySelectorAll("button").every((node) => node.disabled));
    choose(f.view.host, 1, false); // Even a synthetic event cannot mutate frozen state.
    await action.listeners.click();
    assert.equal(f.calls.filter(([name]) => name === "members").length, 1);
    assert.deepEqual(f.calls.find(([name]) => name === "members")[1].moc_ids, [
      "engineering",
      "materials",
    ]);
    const before = f.calls.length;
    resolve(completed());
    await pending;
    assert(f.calls.slice(before).some(([name]) => name === "list"));
    assert.equal(f.view.busy(), false);
    assert.equal(f.view.dirty(), false);
    assert.match(f.view.host.textContent, /后台已确认全部所选关系调整完成/);
  } finally {
    f.close();
  }
});

test("receipts appear before the read and survive a filtered-out entity and catalog failure", async () => {
  const f = await fixture();
  try {
    choose(f.view.host, 0);
    choose(f.view.host, 1);
    f.api.members = async () => completed();
    f.api.list = async () => {
      assert.match(f.view.host.textContent, /后台已确认全部所选关系调整完成/);
      return { ...catalog(), items: [], total: 0 };
    };
    await button(picker(f.view.host), "人工增挂并锁定").listeners.click();
    assert.equal(picker(f.view.host), null);
    assert.match(
      f.view.host.textContent,
      /Synthetic Draft.*Book\/Draft.*工程 MOC：已完成.*材料 MOC：已完成/,
    );
    f.api.list = async () => {
      throw Object.assign(Error("目录仍在观察"), {
        status: 409,
        code: "organization_catalog_pending",
      });
    };
    await f.view.refresh();
    assert.match(f.view.host.textContent, /目录仍在观察/);
    assert.match(
      f.view.host.textContent,
      /最近人工关系操作.*后台已确认全部所选关系调整完成/,
    );
  } finally {
    f.close();
  }
});

test("partial completion retains failed selection and does not replay successful targets", async () => {
  const f = await fixture();
  try {
    choose(f.view.host, 0);
    choose(f.view.host, 1);
    f.api.members = async (body) => {
      f.calls.push(["members", body]);
      return {
        results: [
          { moc_id: "engineering", completed: true },
          {
            moc_id: "materials",
            completed: false,
            error: "maintenance_moc_changed",
          },
        ],
      };
    };
    await button(picker(f.view.host), "人工增挂并锁定").listeners.click();
    assert.deepEqual(
      boxes(f.view.host).map((box) => box.checked),
      [false, true],
    );
    assert.match(
      f.view.host.textContent,
      /部分完成.*工程 MOC：已完成.*材料 MOC：未完成/,
    );
    await f.view.refresh({ automatic: true, force: true });
    assert.equal(f.calls.filter(([name]) => name === "members").length, 1);
  } finally {
    f.close();
  }
});

for (const action of ["link", "unlock"]) {
  test(`${action}: timeout stays unknown after read, blocks replay, and requires explicit new selection`, async () => {
    const f = await fixture();
    try {
      choose(f.view.host, 0);
      choose(f.view.host, 1);
      f.api.members = async (body) => {
        f.calls.push(["members", body]);
        throw Object.assign(Error("timed out"), { code: "timeout" });
      };
      const label =
        action === "link" ? "人工增挂并锁定" : "解除所选关系的人工限制";
      await button(picker(f.view.host), label).listeners.click();
      assert.match(
        f.view.host.textContent,
        /结果未知.*工程 MOC：结果未知.*材料 MOC：结果未知/,
      );
      assert.match(
        f.view.host.textContent,
        /不能证明 Map 写回或人工限制解除已完成/,
      );
      f.data({
        ...catalog(),
        items: [{ ...item, moc_ids: ["engineering", "materials"] }],
      });
      await f.view.refresh();
      await button(picker(f.view.host), label).listeners.click();
      assert.equal(f.calls.filter(([name]) => name === "members").length, 1);
      assert(boxes(f.view.host).every((box) => box.disabled));
      await button(picker(f.view.host), "清空本次选择").listeners.click();
      assert(boxes(f.view.host).every((box) => !box.checked && !box.disabled));
      assert.match(f.view.host.textContent, /结果未知/);
    } finally {
      f.close();
    }
  });
}

test("scope change, revoke and late callbacks cannot expose or submit an old draft", async () => {
  const f = await fixture();
  try {
    choose(f.view.host, 0);
    choose(f.view.host, 1);
    const oldButton = button(picker(f.view.host), "人工增挂并锁定");
    f.scope({ client_id: "c", vault_id: "b" });
    await f.view.refresh();
    await oldButton.listeners.click();
    assert.equal(f.calls.filter(([name]) => name === "members").length, 0);
    assert.deepEqual(
      boxes(f.view.host).map((box) => box.checked),
      [false, false],
    );
    choose(f.view.host, 0);
    let resolve;
    f.api.members = () =>
      new Promise((done) => {
        resolve = done;
      });
    const pending = button(
      picker(f.view.host),
      "人工增挂并锁定",
    ).listeners.click();
    f.fail(Object.assign(Error("revoked"), { status: 403 }));
    await f.view.refresh();
    assert.equal(picker(f.view.host), null);
    assert.doesNotMatch(f.view.host.textContent, /Synthetic Draft|工程 MOC/);
    assert.equal(f.view.busy(), false);
    resolve(completed());
    await pending;
    assert.doesNotMatch(
      f.view.host.textContent,
      /最近人工关系操作|Synthetic Draft/,
    );
    f.fail(null);
    await f.view.refresh();
    assert.deepEqual(
      boxes(f.view.host).map((box) => box.checked),
      [false, false],
    );
  } finally {
    f.close();
  }
});

for (const response of [
  { results: [{ moc_id: "engineering", completed: true }] },
  {
    results: [
      { moc_id: "engineering", completed: true },
      { moc_id: "engineering", completed: true },
      { moc_id: "materials", completed: true },
    ],
  },
  {
    results: [
      { moc_id: "engineering", completed: true },
      { moc_id: "materials", completed: true },
      { moc_id: "unexpected", completed: true },
    ],
  },
]) {
  test("invalid per-target receipt cannot become complete or trigger a retry", async () => {
    const calls = [],
      results = [];
    const fixed = { client_id: "c", vault_id: "v" };
    const session = createMemberSession({
      scope: () => fixed,
      submit: async (body) => {
        calls.push(body);
        return response;
      },
      refresh: async () => {},
      changed: (value) => results.push(value),
    });
    session.activate(fixed);
    const entry = session.entry(item, mocs);
    entry.selected = new Set(mocs.map((moc) => moc.id));
    await session.apply(entry, "link");
    assert.equal(results.at(-1).phase, "unknown");
    await session.apply(entry, "link");
    assert.equal(calls.length, 1);
  });
}

test("catalog-pending preflight preserves targets and allows an explicit retry after reading", async () => {
  const f = await fixture();
  try {
    choose(f.view.host, 0);
    choose(f.view.host, 1);
    f.api.members = async (body) => {
      f.calls.push(["members", body]);
      throw Object.assign(Error("pending"), {
        status: 409,
        code: "organization_catalog_pending",
      });
    };
    await button(picker(f.view.host), "人工增挂并锁定").listeners.click();
    assert.match(f.view.host.textContent, /目录仍在观察中/);
    assert.doesNotMatch(f.view.host.textContent, /结果未知/);
    assert(boxes(f.view.host).every((node) => node.checked && !node.disabled));
    assert.equal(f.calls.filter(([name]) => name === "members").length, 1);
    f.api.members = async (body) => {
      f.calls.push(["members", body]);
      return completed();
    };
    await button(picker(f.view.host), "人工增挂并锁定").listeners.click();
    assert.equal(f.calls.filter(([name]) => name === "members").length, 2);
    assert.match(f.view.host.textContent, /后台已确认全部所选关系调整完成/);
  } finally {
    f.close();
  }
});

test("an unresolved entity receipt stays visible after an operation on another entity", async () => {
  const f = await fixture();
  try {
    f.data({
      ...catalog(),
      items: [
        { ...item },
        { ...item, id: "other", title: "Other Draft", path: "Book/Other" },
      ],
      total: 2,
    });
    await f.view.refresh();
    choose(f.view.host, 0);
    f.api.members = async () => {
      throw Object.assign(Error("timeout"), { code: "timeout" });
    };
    await button(picker(f.view.host), "人工增挂并锁定").listeners.click();
    const other = f.view.host.querySelector('[data-manual-entity="other"]');
    const otherBox = other
      .querySelectorAll("input")
      .find((node) => node.type === "checkbox");
    otherBox.checked = true;
    otherBox.listeners.change();
    f.api.members = async () => ({
      results: [{ moc_id: "engineering", completed: true }],
    });
    await button(other, "人工增挂并锁定").listeners.click();
    assert.match(
      f.view.host.textContent,
      /最近人工关系操作.*Other Draft.*已完成.*尚待核对的人工关系操作.*Synthetic Draft.*结果未知/,
    );
    assert(boxes(f.view.host).every((node) => node.disabled));
  } finally {
    f.close();
  }
});

for (const status of [401, 403, 404]) {
  test(`membership ${status} clears the entire scope session and old result`, async () => {
    const f = await fixture();
    try {
      choose(f.view.host, 0);
      f.api.members = async () => {
        throw Object.assign(Error("unavailable"), { status });
      };
      await button(picker(f.view.host), "人工增挂并锁定").listeners.click();
      assert.equal(picker(f.view.host), null);
      assert.doesNotMatch(
        f.view.host.textContent,
        /Synthetic Draft|工程 MOC|最近人工关系操作/,
      );
      assert.equal(f.view.dirty(), false);
    } finally {
      f.close();
    }
  });
}

test("a post-write HTTP failure stays unknown and does not retry", async () => {
  const f = await fixture();
  try {
    choose(f.view.host, 0);
    choose(f.view.host, 1);
    f.api.members = async (body) => {
      f.calls.push(["members", body]);
      throw Object.assign(Error("proof failed"), {
        status: 500,
        code: "organization_manual_projection_incomplete",
      });
    };
    await button(picker(f.view.host), "人工增挂并锁定").listeners.click();
    await f.view.refresh();
    assert.match(f.view.host.textContent, /结果未知/);
    assert.doesNotMatch(f.view.host.textContent, /均未写入|全部.*完成/);
    assert.equal(f.calls.filter(([name]) => name === "members").length, 1);
  } finally {
    f.close();
  }
});
