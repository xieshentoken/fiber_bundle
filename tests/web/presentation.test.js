import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createClientsView } from "../../src/fiber_bundle/web/clients.js";
import { createPolicyEditor } from "../../src/fiber_bundle/web/models_policy.js";
import {
  recordHeading,
  showJSON,
} from "../../src/fiber_bundle/web/record_ui.js";
import { selectPage } from "../../src/fiber_bundle/web/navigation.js";
import { createCreationView } from "../../src/fiber_bundle/web/creation.js";
import { createBackupsView } from "../../src/fiber_bundle/web/backups.js";

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.events = {};
    this.attributes = {};
    this.dataset = {};
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
  }
  append(...nodes) {
    this.children.push(...nodes);
  }
  replaceChildren(...nodes) {
    this.children = nodes;
  }
  setAttribute(key, value) {
    this.attributes[key] = value;
  }
  removeAttribute(key) {
    delete this.attributes[key];
  }
  addEventListener(name, callback) {
    this.events[name] = callback;
  }
  reset() {}
  reportValidity() {
    return true;
  }
}
function environment(work) {
  const previous = { document: globalThis.document, window: globalThis.window };
  const nodes = new Map();
  globalThis.document = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, new Element("div"));
      return nodes.get(id);
    },
    createElement: (tag) => new Element(tag),
    createTextNode: (text) => ({ textContent: text }),
  };
  globalThis.window = { confirm: () => true };
  return Promise.resolve()
    .then(() => work(nodes))
    .finally(() => {
      globalThis.document = previous.document;
      globalThis.window = previous.window;
    });
}

test("backup list distinguishes a completion marker from integrity verification", () =>
  environment(async (nodes) => {
    const command = "fiber-backup verify --bundle '/synthetic bundle'";
    const view = createBackupsView({
      backupAutomation: async () => ({
        revision: 0,
        policy: {},
        key_configured: false,
        jobs: [],
      }),
      backups: async () => [
        {
          id: "one",
          action: "create",
          state: "encrypted_complete",
          destination: "/synthetic bundle",
          ciphertext_verified: false,
          verification_command: command,
        },
      ],
    });
    document.getElementById("backups-legacy").open = true;
    await view.refresh();
    const card = nodes.get("backups-content").children[0];
    assert.equal(
      card.children[0].children[1].textContent,
      "完成标记已记录，尚未校验备份",
    );
    assert.equal(
      card.children[0].children[1].className.includes("waiting"),
      true,
    );
    assert.equal(card.children[3].children[0].textContent, command);
    assert.equal(card.children.at(-1).textContent, "准备干净恢复");
    view.clear();
  }));

test("technical details retain all values as literal text", () =>
  environment(() => {
    const root = new Element("article");
    const value = {
      evidence: '<img src="x" onerror="unsafe()">',
      charged_nusd: 0,
    };
    showJSON(root, value, "费用明细");
    const [details] = root.children;
    assert.equal(details.tagName, "details");
    assert.equal(details.children[0].textContent, "费用明细");
    assert.deepEqual(JSON.parse(details.children[1].textContent), value);
    assert.equal(details.children[1].innerHTML, undefined);
    const heading = recordHeading("备份", "等待离线执行", "waiting");
    assert.equal(heading.children[1].textContent, "等待离线执行");
  }));

test("navigation changes one panel and scrolls its own container", () =>
  environment((nodes) => {
    const one = new Element("button"),
      two = new Element("button");
    one.dataset = { tab: "clients", label: "客户端权限" };
    two.dataset = { tab: "backups", label: "备份与恢复" };
    const nav = {
      scrollLeft: 0,
      getBoundingClientRect: () => ({ left: 20, right: 400 }),
    };
    two.parentElement = nav;
    two.getBoundingClientRect = () => ({ left: 360, right: 480 });
    globalThis.document.querySelectorAll = () => [one, two];
    selectPage(two);
    assert.equal(nodes.get("clients-panel").hidden, true);
    assert.equal(nodes.get("backups-panel").hidden, false);
    assert.equal(nodes.get("page-name").textContent, "备份与恢复");
    assert.equal(two.attributes["aria-current"], "page");
    assert.equal(one.attributes["aria-current"], undefined);
    assert.equal(nav.scrollLeft, 88);
  }));

test("late client creation cannot restore credentials after session clear", () =>
  environment(async (nodes) => {
    let releaseRefresh;
    const refresh = new Promise((resolve) => {
      releaseRefresh = resolve;
    });
    const view = createClientsView(
      {
        createClient: async () => ({
          id: "synthetic-client",
          token: "synthetic-token",
        }),
      },
      () => refresh,
    );
    const button = new Element("button");
    const submission = nodes
      .get("create-client")
      .events.submit({ preventDefault() {}, submitter: button });
    await Promise.resolve();
    view.clear();
    releaseRefresh();
    await submission;
    assert.equal(nodes.get("new-token").value, "");
    assert.equal(nodes.get("token-panel").hidden, true);
    assert.equal(button.disabled, false);
  }));

test("unchanged grants preserve edits; a changed grant replaces them", () =>
  environment((nodes) => {
    const view = createClientsView({}, async () => {});
    const vaults = [{ id: "vault", name: "专用库", status: "active" }];
    const clients = [
      {
        id: "client",
        name: "专用客户端",
        active: true,
        grants: [
          {
            vault_id: "vault",
            can_query: true,
            can_original: false,
            can_add: true,
          },
        ],
      },
    ];
    view.render(vaults, clients);
    const target = nodes.get("client-permissions");
    const form = target.children[1].children[1];
    form.children[0].children[0].checked = false;
    view.render(
      globalThis.structuredClone(vaults),
      globalThis.structuredClone(clients),
    );
    assert.equal(target.children[1].children[1], form);
    assert.equal(form.children[0].children[0].checked, false);
    const changed = globalThis.structuredClone(clients);
    changed[0].grants[0].can_add = false;
    view.render(vaults, changed);
    assert.notEqual(target.children[1].children[1], form);
    assert.equal(
      target.children[1].children[1].children[2].children[0].checked,
      false,
    );
  }));

test("model drafts keep the original revision across server changes until explicit discard", () =>
  environment(async (nodes) => {
    let server = {
      configuration_sha256: "original",
      configured: true,
      value: {
        local_query: { model: "saved-model" },
        cloud_answer: null,
        approved_backups: [],
      },
    };
    const editor = createPolicyEditor({
      queryPolicy: async () => globalThis.structuredClone(server),
    });
    await editor.refresh();
    const fields = nodes.get("query-policy-fields");
    const input = fields.children[0].children[1].children[0];
    input.value = "unsaved-model";
    input.events.input();
    await editor.refresh();
    assert.equal(fields.children[0].children[1].children[0], input);
    server.configuration_sha256 = "new-version";
    await editor.refresh();
    assert.equal(editor.digest, "original");
    assert.equal(input.value, "unsaved-model");
    globalThis.window.confirm = () => false;
    await nodes.get("query-policy-discard").events.click();
    assert.equal(editor.digest, "original");
    globalThis.window.confirm = () => true;
    await nodes.get("query-policy-discard").events.click();
    assert.equal(editor.digest, "new-version");
    assert.equal(
      fields.children[0].children[1].children[0].value,
      "saved-model",
    );
  }));

test("cleared policy ignores a late server read", () =>
  environment(async (nodes) => {
    let complete;
    const result = new Promise((resolve) => {
      complete = resolve;
    });
    const editor = createPolicyEditor({ queryPolicy: () => result });
    const reading = editor.refresh();
    editor.clear();
    complete({
      configuration_sha256: "late",
      configured: true,
      value: {
        local_query: { model: "late-model" },
        cloud_answer: null,
        approved_backups: [],
      },
    });
    await reading;
    assert.equal(editor.digest, null);
    assert.equal(nodes.get("query-policy-fields").children[0].tagName, "p");
  }));

test("desktop offline token copy matches the authoritative web tokens", () => {
  const web = new URL("../../src/fiber_bundle/web/tokens.css", import.meta.url);
  const desktop = new URL("../../desktop/tokens.css", import.meta.url);
  assert.equal(readFileSync(web, "utf8"), readFileSync(desktop, "utf8"));
});

test("document requirements survive refresh while eligible inputs are unchanged", () =>
  environment(async (nodes) => {
    const view = createCreationView({
      creationTasks: async () => ({ tasks: [] }),
      clients: async () => [{ id: "client", name: "合成身份", active: true }],
      queryTasks: async () => ({
        tasks: [
          {
            task_id: "query",
            client_id: "client",
            role: "local",
            state: "succeeded",
          },
        ],
      }),
      vaults: async () => [],
    });
    await view.refresh();
    const form = nodes.get("creation-form");
    const requirements = form.children[3].children[0];
    requirements.value = "尚未提交的制作要求";
    await view.refresh();
    assert.equal(form.children[3].children[0], requirements);
    assert.equal(requirements.value, "尚未提交的制作要求");
    view.clear();
    assert.equal(form.children.length, 0);
  }));

test("model save preserves edits made while the earlier configuration is pending", () =>
  environment(async (nodes) => {
    let finish;
    const editor = createPolicyEditor({
      queryPolicy: async () => ({
        configuration_sha256: "original",
        configured: true,
        value: {
          local_query: { model: "saved-model" },
          cloud_answer: null,
          approved_backups: [],
        },
      }),
      saveQueryPolicy: ({ value }) => {
        const submitted = globalThis.structuredClone(value);
        return new Promise((resolve) => {
          finish = () =>
            resolve({ configuration_sha256: "submitted", value: submitted });
        });
      },
    });
    await editor.refresh();
    const input = nodes.get("query-policy-fields").children[0].children[1]
      .children[0];
    input.value = "submitted-model";
    input.events.input();
    const form = nodes.get("query-policy-form");
    const saving = form.events.submit({
      preventDefault() {},
      target: form,
      submitter: new Element("button"),
    });
    input.value = "newer-model";
    input.events.input();
    finish();
    await saving;
    assert.equal(editor.digest, "submitted");
    assert.equal(editor.hasUnsavedChanges, true);
    assert.equal(input.value, "newer-model");
    assert.equal(
      nodes.get("query-policy-fields").children[0].children[1].children[0],
      input,
    );
  }));

test("model reversion and a save pending reversion compare the confirmed snapshot", () =>
  environment(async (nodes) => {
    let finish;
    const value = {
      local_query: { model: "a", max_calls: 1 },
      cloud_answer: null,
      approved_backups: [],
    };
    const editor = createPolicyEditor({
      queryPolicy: async () => ({
        configuration_sha256: "a",
        configured: true,
        value,
      }),
      saveQueryPolicy: ({ value: submitted }) =>
        new Promise((resolve) => {
          finish = () =>
            resolve({ configuration_sha256: "b", value: submitted });
        }),
    });
    await editor.refresh();
    const input = nodes.get("query-policy-fields").children[0].children[1]
      .children[0];
    for (const v of ["b", "a"]) {
      input.value = v;
      input.events.input();
    }
    assert.equal(editor.hasUnsavedChanges, false);
    input.value = "b";
    input.events.input();
    const form = nodes.get("query-policy-form");
    const saving = form.events.submit({
      preventDefault() {},
      target: form,
      submitter: new Element("button"),
    });
    input.value = "a";
    input.events.input();
    finish();
    await saving;
    assert.equal(editor.hasUnsavedChanges, true);
    input.value = "b";
    input.events.input();
    assert.equal(editor.hasUnsavedChanges, false);
  }));
