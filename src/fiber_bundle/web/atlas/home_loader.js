import { visibleVault } from "./vault_visibility.js";
// Read-only overview records for Home and Inbox, through the shared bounded home reads.
// One GET at a time; a missing response stays missing (never zero or healthy).
import { createHomeData } from "../home_data.js";
import { costWindow } from "../home_model.js";

const FIRST = ["channels", "backend", "executions", "vaults"];
const REST = [
  "clients",
  "batches",
  "documents",
  "media",
  "articles",
  "components",
  "query",
  "source",
  "agents",
  "backups",
];

export function createHomeLoader(api, session, onChange) {
  const data = createHomeData(api.homeRead);
  const records = {};
  let epoch = 0,
    running = null,
    scope = {},
    timer = null;
  async function read(name, params = {}, force = false) {
    const current = epoch;
    const record = await data.get(name, params, {
      ttl: name === "usage" ? 300000 : 60000,
      force,
    });
    if (current !== epoch || !session.csrf || !record) return null;
    records[name] =
      name === "vaults" && Array.isArray(record.value)
        ? { ...record, value: record.value.filter(visibleVault) }
        : record;
    onChange(records);
    return record;
  }
  // Import scope: an active identity allowed to add originals, and its default vault.
  function chooseScope(preferred = {}) {
    const clients = records.clients?.value?.filter((c) => c.active) || [];
    const vaults =
      records.vaults?.value?.filter((v) => v.status === "active") || [];
    const allowed = (client) =>
      client.grants
        .filter((g) => g.can_add && g.can_original)
        .map((g) => g.vault_id);
    const client =
      clients.find((c) => c.id === preferred.client_id && allowed(c).length) ||
      clients.find((c) => allowed(c).length);
    const permitted = client
      ? vaults.filter((v) => allowed(client).includes(v.id))
      : [];
    const vault =
      permitted.find((v) => v.id === preferred.vault_id) ||
      permitted.find((v) => v.default_query) ||
      permitted[0];
    return {
      clients: clients.filter((c) => allowed(c).length),
      vaults: permitted,
      scope:
        client && vault ? { client_id: client.id, vault_id: vault.id } : {},
    };
  }
  async function loadScope(force) {
    for (const name of ["imports", "expert", "videos", "automatic"])
      delete records[name];
    data.setScope(JSON.stringify([session.revision, scope]));
    onChange(records);
    if (!scope.client_id) return;
    const current = epoch;
    for (const name of ["imports", "expert", "videos"]) {
      await read(name, scope, force);
      if (current !== epoch) return;
    }
    if (records.components?.value?.gui_features?.unified_workflow)
      await read("automatic", { vault_id: scope.vault_id }, force);
  }
  async function load(force) {
    const current = epoch;
    data.setScope(JSON.stringify([session.revision, scope]));
    for (const name of [...FIRST, ...REST]) {
      await read(name, {}, force);
      if (current !== epoch || !session.csrf) return;
    }
    if (!records.components?.value?.gui_features?.index_summary)
      delete records.index;
    scope = chooseScope(scope).scope;
    await loadScope(force);
    if (current !== epoch) return;
    if (records.components?.value?.gui_features?.index_summary)
      await read("index", { limit: "20", offset: "0" }, force);
    await read("usage", { ...costWindow(), api_key_id: "all" }, force);
  }
  function refresh(force = false) {
    if (running) return running;
    const current = epoch;
    running = load(force).finally(() => {
      if (current === epoch) running = null;
    });
    return running;
  }
  function poll() {
    if (document.hidden || !session.csrf || running) return;
    const now = Date.now();
    const busyChannels = records.channels?.value?.channels?.some(
      (c) => c.state === "running",
    );
    const busyRuns = records.executions?.value?.runs?.some((r) =>
      ["queued", "running", "waiting_budget"].includes(r.state),
    );
    const buffering = records.batches?.value?.some?.(
      (b) => b.state === "buffering",
    );
    for (const [name, period] of [
      ["channels", busyChannels ? 10000 : 60000],
      ["executions", busyRuns ? 30000 : 60000],
      ["batches", buffering ? 30000 : 60000],
      ["vaults", 60000],
    ]) {
      const record = records[name];
      if (record && !record.error && now - record.observed >= period)
        void read(name, {}, true);
    }
  }
  return {
    records,
    refresh,
    start() {
      timer ??= setInterval(poll, 5000);
    },
    get scope() {
      return { ...scope };
    },
    scopeChoices: () => chooseScope(scope),
    setScope(next) {
      epoch++;
      data.cancel();
      running = null;
      scope = chooseScope(next).scope;
      return loadScope(false);
    },
    dispose() {
      epoch++;
      clearInterval(timer);
      timer = null;
      data.clear();
    },
  };
}
