import { el } from "./ui.js";
import { createGraphScopeControls } from "./graph_scope_controls.js";
import {
  authorizedGraphScopes,
  sameGraphScope,
} from "./graph_scope_preferences.js";
import { createGraphScopeReads } from "./graph_scope_read.js";
import { noteCloudSnapshot, noteCloudSearch } from "./note_cloud_model.js";
import { createNoteCloudExplorer } from "./note_cloud_explorer.js";
import { cloudText as t } from "./note_cloud_copy.js";
import { setText } from "./i18n/index.js";
import { cloudNodeLabel, cloudNodeKind } from "./note_cloud_presentation.js";
import { joinText } from "./i18n/index.js";

// A read-only page shared by classic and Atlas. No task, model or index mutations.
export function createNoteCloudView(
  api,
  { loadRenderer = () => import("/static/note_cloud_renderer.bundle.js") } = {},
) {
  const root = el("div", null, "note-cloud"),
    who = el("select"),
    where = el("select"),
    scopeBar = el("div", null, "nc-scope"),
    status = el("p", null, "nc-status"),
    coverage = el("div", null, "nc-coverage"),
    tool = el("div", null, "nc-toolbar"),
    search = el("input"),
    results = el("div", null, "nc-results"),
    feedback = el("p", null, "nc-feedback");
  let clients = [],
    vaults = [],
    entered = false,
    active = false,
    revision = 0,
    searchRevision = 0,
    fixedData = null;
  const scope = () => ({ client_id: who.value, vault_id: where.value });
  const controls = createGraphScopeControls(scope);
  const reads = createGraphScopeReads(controls.readState);
  for (const node of [who, where, search]) node.dataset.draft = "ignore";
  who.setAttribute("aria-label", t("identity"));
  where.setAttribute("aria-label", t("vault"));
  status.setAttribute("role", "status");
  feedback.setAttribute("role", "status");
  search.type = "search";
  search.maxLength = 200;
  setText(search, t("search"), "placeholder");
  setText(search, t("search"), "aria-label");
  function button(label, work, className = "") {
    const node = el("button", label, className);
    node.type = "button";
    node.addEventListener("click", () =>
      Promise.resolve().then(work).catch(showError),
    );
    return node;
  }
  function showError(error) {
    feedback.textContent = String(error?.message || error);
  }
  const refreshButton = button(t("refresh"), () => refresh());
  refreshButton.dataset.pageRefresh = "true";
  const find = button(t("find"), () => findNotes());
  const form = el("form", null, "nc-search");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    find.click();
  });
  form.append(search, find);
  const explorer = createNoteCloudExplorer({
    button,
    scope,
    loadRenderer,
    onFeedback: (text) => {
      setText(feedback, text);
    },
  });
  tool.append(form, ...explorer.tools);
  for (const [label, node] of [
    [t("identity"), who],
    [t("vault"), where],
  ]) {
    const field = el("label", label);
    field.append(node);
    scopeBar.append(field);
  }
  const defaults = el("details", null, "nc-defaults");
  defaults.append(el("summary", t("defaults")), controls.host);
  scopeBar.append(refreshButton, defaults);
  root.append(
    scopeBar,
    tool,
    results,
    status,
    coverage,
    explorer.node,
    el("p", t("legend"), "nc-legend"),
    el("p", t("hint"), "nc-hint"),
    explorer.list,
    feedback,
  );

  function resetData() {
    root.removeAttribute("aria-busy");
    explorer.clear();
    fixedData = null;
    coverage.replaceChildren();
    results.replaceChildren();
    feedback.textContent = "";
    status.textContent = "";
  }
  function paintCoverage() {
    const data = fixedData,
      c = data.coverage;
    setText(
      status,
      t("count", {
        notes: c.current_notes,
        experts: c.current_experts,
        missing: c.unresolved_targets,
        edges: data.edges.length,
      }),
    );
    coverage.replaceChildren();
    if (c.index_state && c.index_state !== "ready")
      coverage.append(el("p", t("indexState", { state: c.index_state })));
    if (c.omitted_nodes || c.omitted_edges)
      coverage.append(
        el(
          "p",
          t("partial", {
            nodes: c.omitted_nodes || 0,
            edges: c.omitted_edges || 0,
          }),
        ),
      );
    if (!c.complete || c.unavailable_count)
      coverage.append(el("p", t("incomplete")));
    if (c.membership_edges)
      coverage.append(
        el(
          "p",
          t("membershipCounts", {
            count: c.membership_edges,
            manual: c.manual_memberships,
            system: c.system_memberships,
            locked: c.manual_locked_memberships,
          }),
        ),
      );
    if (c.has_unresolved)
      coverage.append(
        el(
          "p",
          t("unresolvedCounts", { count: c.unresolved_targets }),
          "nc-target-warning",
        ),
      );
    if (c.sync_not_synced || c.sync_unknown)
      coverage.append(
        el(
          "p",
          t("syncCounts", {
            present: c.sync_indexed_present,
            unsynced: c.sync_not_synced,
            unknown: c.sync_unknown,
          }),
        ),
      );
    if (c.excluded_expert_entries)
      coverage.append(
        el("p", t("excludedExperts", { count: c.excluded_expert_entries })),
      );
    if (c.moc_membership_unavailable)
      coverage.append(
        el(
          "p",
          t("membershipMissing", { count: c.moc_membership_unavailable }),
        ),
      );
  }
  async function loadScope(focus = null) {
    const fixed = scope();
    const mine = ++revision;
    searchRevision++;
    find.disabled = false;
    resetData();
    if (!fixed.client_id || !fixed.vault_id) {
      setText(status, t("noScope"));
      return;
    }
    setText(status, t("loading"));
    root.setAttribute("aria-busy", "true");
    try {
      const value = await api.graph.noteCloud(
        fixed.client_id,
        fixed.vault_id,
        focus,
      );
      if (!active || mine !== revision || !sameGraphScope(fixed, scope()))
        return;
      fixedData = noteCloudSnapshot(value, fixed);
      paintCoverage();
      await explorer.setData(fixedData, {
        focus,
        admitted: () =>
          active && mine === revision && sameGraphScope(fixed, scope()),
      });
    } catch (error) {
      if (!active || mine !== revision) return;
      resetData();
      setText(status, t("failed", { message: error.message }));
      throw error;
    } finally {
      if (mine === revision) root.removeAttribute("aria-busy");
    }
  }
  async function findNotes() {
    const query = search.value.trim();
    if (!query || !scope().vault_id) {
      results.replaceChildren();
      return;
    }
    const turn = ++searchRevision,
      generation = revision,
      fixed = scope();
    find.disabled = true;
    try {
      const value = noteCloudSearch(
        await api.graph.noteCloudSearch(fixed.client_id, fixed.vault_id, query),
        fixed,
      );
      if (
        !active ||
        turn !== searchRevision ||
        generation !== revision ||
        !sameGraphScope(fixed, scope())
      )
        return;
      results.replaceChildren(
        el(
          "p",
          value.items.length
            ? t("resultCount", {
                count: value.matched_count ?? value.items.length,
                suffix: value.truncated ? t("truncated") : "",
              })
            : t("noMatches"),
        ),
      );
      for (const node of value.items) {
        const generationAtSearch = revision;
        const item = button(
          joinText(
            [cloudNodeKind(node), cloudNodeLabel(node), node.path],
            " · ",
          ),
          async () => {
            if (
              !active ||
              generationAtSearch !== revision ||
              !sameGraphScope(fixed, scope())
            )
              return;
            if (
              value.snapshot_token === fixedData?.snapshot_token &&
              explorer.hasNode(node.id)
            )
              explorer.select(node.id);
            else
              await reads.run(
                "focus:" + node.id + ":" + JSON.stringify(fixed),
                () => loadScope(node.id),
              );
          },
          "nc-note-button",
        );
        results.append(item);
      }
    } catch (error) {
      if (active && turn === searchRevision && generation === revision) {
        if ([401, 403].includes(error.status)) {
          revision++;
          resetData();
        }
        results.replaceChildren(
          el("p", t("searchFailed", { message: error.message })),
        );
      }
    } finally {
      if (turn === searchRevision) find.disabled = false;
    }
  }
  function chooseVaults() {
    const allowed = authorizedGraphScopes(clients, vaults).filter(
      (s) => s.client_id === who.value,
    );
    where.replaceChildren();
    for (const value of allowed) {
      const option = el(
        "option",
        vaults.find((v) => v.id === value.vault_id).name,
      );
      option.value = value.vault_id;
      where.append(option);
    }
    where.value = allowed[0]?.vault_id || "";
  }
  async function scopeChanged(clientChanged = false) {
    reads.cancel();
    revision++;
    searchRevision++;
    find.disabled = false;
    if (clientChanged) chooseVaults();
    controls.changed();
    search.value = "";
    return reads.run("scope:" + JSON.stringify(scope()), () => loadScope());
  }
  who.addEventListener("change", () => scopeChanged(true).catch(showError));
  where.addEventListener("change", () => scopeChanged().catch(showError));
  function refresh() {
    active = true;
    return reads
      .run("catalog:" + JSON.stringify(scope()), async ({ current }) => {
        const mine = ++revision;
        searchRevision++;
        find.disabled = false;
        resetData();
        const [nextClients, nextVaults] = await Promise.all([
          api.clients(),
          api.vaults(),
        ]);
        if (!active || !current() || mine !== revision) return;
        clients = nextClients;
        vaults = nextVaults;
        const choice = controls.catalog(clients, vaults, scope(), !entered);
        if (controls.host.querySelector('[role="status"]')?.textContent)
          defaults.open = true;
        entered = true;
        who.replaceChildren();
        const allowed = authorizedGraphScopes(clients, vaults);
        for (const client of clients.filter((c) =>
          allowed.some((s) => s.client_id === c.id),
        )) {
          const option = el("option", client.name);
          option.value = client.id;
          who.append(option);
        }
        who.value = choice?.client_id || "";
        chooseVaults();
        where.value = choice?.vault_id || "";
        controls.changed();
        await loadScope();
      })
      .catch((error) => {
        if (active) {
          resetData();
          setText(status, t("failed", { message: error.message }));
        }
        throw error;
      });
  }
  function onKey(event) {
    if (active && event.key === "Escape") {
      explorer.clearSelection();
    }
  }
  function clearView() {
    active = false;
    revision++;
    searchRevision++;
    reads.cancel();
    entered = false;
    clients = [];
    vaults = [];
    who.replaceChildren();
    where.replaceChildren();
    who.value = where.value = search.value = "";
    find.disabled = false;
    resetData();
    controls.reset();
  }
  window.addEventListener("pagehide", clearView);
  document.addEventListener("keydown", onKey);
  return {
    node: root,
    refresh,
    clear: clearView,
    dispose() {
      clearView();
      window.removeEventListener("pagehide", clearView);
      document.removeEventListener("keydown", onKey);
    },
  };
}
