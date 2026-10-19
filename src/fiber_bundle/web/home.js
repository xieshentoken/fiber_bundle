// Home lifecycle and scope selection. Rendering and evidence rules are separate.
import { $, el } from "./ui.js";
import { createHomeData } from "./home_data.js";
import { renderHome, renderHomeHeader } from "./home_render.js";
import { renderInbox } from "./inbox.js";
import { costWindow, homeVaults } from "./home_model.js";
export function createHomeView(api, session) {
  const data = createHomeData(api.homeRead),
    records = {};
  let epoch = 0,
    visiblePage = "",
    build = null,
    scopeLabel = "",
    scope = {},
    refreshing = null,
    entered = false;
  function draw() {
    if (visiblePage === "home") {
      const count = renderHome(
        $("home-content"),
        records,
        build,
        scopeLabel,
        scope,
        draw,
      );
      renderHomeHeader($("page-extra"), $("page-extra-end"), records, count);
    }
    if (visiblePage === "worklist")
      renderInbox($("worklist-content"), records, scopeLabel, draw);
  }
  async function read(name, params = {}, force = false) {
    const current = epoch;
    const record = await data.get(name, params, {
      ttl: name === "usage" ? 300000 : 60000,
      force,
    });
    if (current !== epoch || !session.csrf || !record) return null;
    records[name] = record;
    draw();
    return record;
  }
  const scopeKey = () =>
    JSON.stringify([
      session.revision,
      scope,
      records.clients?.value?.map((c) => [c.id, c.active, c.grants]),
    ]);
  function populateScope() {
    const clients = records.clients?.value?.filter((c) => c.active) || [],
      vaults = homeVaults(records.vaults?.value || []).filter(
        (v) =>
          v.status === "active" &&
          (v.attachment_state || "attached") === "attached",
      );
    const cs = $("home-client"),
      vs = $("home-vault");
    const selectedClient =
      clients.find((c) => c.id === cs.value) ||
      clients.find((c) => c.grants.some((g) => g.can_add && g.can_original));
    cs.replaceChildren();
    for (const c of clients) {
      const option = el("option", c.name);
      option.value = c.id;
      cs.append(option);
    }
    if (selectedClient) cs.value = selectedClient.id;
    const permitted = vaults.filter((v) =>
      selectedClient?.grants.some(
        (g) => g.vault_id === v.id && g.can_add && g.can_original,
      ),
    );
    const selectedVault =
      permitted.find((v) => v.id === vs.value) ||
      permitted.find((v) => v.default_query) ||
      permitted[0];
    vs.replaceChildren();
    for (const v of permitted) {
      const option = el("option", v.name);
      option.value = v.id;
      vs.append(option);
    }
    if (selectedVault) vs.value = selectedVault.id;
    scope =
      selectedClient && selectedVault
        ? { client_id: selectedClient.id, vault_id: selectedVault.id }
        : {};
    scopeLabel = scope.client_id
      ? `${selectedClient.name} / ${selectedVault.name}`
      : "";
  }
  async function loadScope(force = false) {
    if (data.setScope(scopeKey())) {
      for (const name of ["imports", "expert", "automatic", "videos"])
        delete records[name];
      draw();
    }
    if (!scope.client_id) {
      draw();
      return;
    }
    const current = epoch;
    for (const name of ["imports", "expert"]) {
      await read(name, scope, force);
      if (current !== epoch) return;
    }
    if (records.components?.value?.gui_features?.unified_workflow)
      await read("automatic", { vault_id: scope.vault_id }, force);
    if (records.components?.value?.gui_features?.video_processing)
      await read("videos", scope, force);
  }
  for (const id of ["home-client", "home-vault"])
    $(id).addEventListener("change", () => {
      pause();
      populateScope();
      for (const name of ["imports", "expert", "automatic", "videos"])
        delete records[name];
      draw();
      void loadScope();
    });
  async function load(force) {
    const current = epoch;
    data.setScope(scopeKey());
    // First screen: four reads, in addition to health/session performed by the shell.
    for (const name of ["channels", "backend", "executions", "vaults"]) {
      await read(name, {}, force);
      if (current !== epoch || !session.csrf || !visiblePage) return;
    }
    for (const name of [
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
    ]) {
      await read(name, {}, force);
      if (current !== epoch || !session.csrf || !visiblePage) return;
    }
    if (!records.components?.value?.gui_features?.index_summary)
      delete records.index;
    if (!records.components?.value?.gui_features?.unified_workflow)
      delete records.automatic;
    if (!records.components?.value?.gui_features?.video_processing)
      delete records.videos;
    populateScope();
    await loadScope(force);
    if (current !== epoch) return;
    if (records.components?.value?.gui_features?.index_summary)
      await read("index", { limit: "20", offset: "0" }, force);
    await read("usage", { ...costWindow(), api_key_id: "all" }, force);
    draw();
  }
  function refresh(force = false) {
    if (refreshing) {
      if (!force) return refreshing;
      const current = epoch;
      // An explicit refresh must read again after the current load, in the same scope.
      return refreshing.then(() =>
        current === epoch && visiblePage && session.csrf
          ? refresh(true)
          : undefined,
      );
    }
    const current = epoch;
    refreshing = load(force).finally(() => {
      if (current === epoch) refreshing = null;
    });
    return refreshing;
  }
  function pause() {
    epoch++;
    data.cancel();
    refreshing = null;
  }
  function clear() {
    pause();
    data.clear();
    for (const key of Object.keys(records)) delete records[key];
    scope = {};
    scopeLabel = "";
    $("home-client").replaceChildren();
    $("home-vault").replaceChildren();
    $("home-content").replaceChildren();
    $("worklist-content").replaceChildren();
  }
  function setPage(page) {
    pause();
    visiblePage = ["home", "worklist"].includes(page) ? page : "";
    if (page === "home" && !entered) {
      entered = true;
      $("home-content").dataset.enter = "true";
      $("home-content").addEventListener(
        "animationend",
        () => delete $("home-content").dataset.enter,
        { once: true },
      );
    }
  }
  window.matchMedia("(max-width: 767px)").addEventListener("change", draw);
  const timer = setInterval(() => {
    if (document.hidden || !visiblePage || !session.csrf || refreshing) return;
    const now = Date.now(),
      active = records.channels?.value?.channels.some(
        (c) => c.state === "running",
      );
    for (const [name, period] of [
      ["channels", active ? 10000 : 60000],
      [
        "executions",
        records.executions?.value?.runs.some((r) =>
          ["queued", "running", "waiting_budget"].includes(r.state),
        )
          ? 30000
          : 60000,
      ],
      [
        "batches",
        records.batches?.value?.some((b) => b.state === "buffering") ||
        visiblePage === "worklist"
          ? 30000
          : 60000,
      ],
      ["vaults", 60000],
    ]) {
      const r = records[name];
      if (r && !r.error && now - r.observed >= period)
        void read(name, {}, true);
    }
    const automatic = records.automatic;
    const automaticActive = automatic?.value?.some(
      (job) =>
        job.action_required ||
        ["queued", "claimed", "waiting", "reviewed"].includes(job.state),
    );
    if (
      scope.vault_id &&
      automatic &&
      !automatic.error &&
      now - automatic.observed >= (automaticActive ? 30000 : 60000)
    )
      void read("automatic", { vault_id: scope.vault_id }, true);
    if (
      records.usage &&
      !records.usage.error &&
      now - records.usage.observed >= 300000
    )
      void read("usage", { ...costWindow(), api_key_id: "all" }, true);
  }, 5000);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pause();
    else if (visiblePage && session.csrf) void refresh();
  });
  $("home-content").addEventListener("click", (event) => {
    if (event.target.closest("[data-home-refresh]")) void refresh(true);
  });
  return {
    refresh,
    clear,
    pause,
    setPage,
    setBuild(value) {
      build = value?.build;
    },
    dispose() {
      clearInterval(timer);
      clear();
    },
  };
}
