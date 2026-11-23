// Shared classic/Atlas controls. Saving a default never starts a scope read.
import { el } from "./ui.js";
import { msg, setText, sourceText } from "./i18n/index.js";
import {
  createGraphScopePreferences,
  authorizedGraphScopes,
  resolveGraphScope,
  sameGraphScope,
} from "./graph_scope_preferences.js";
const copy = (key, parameters) => msg("graph_scope." + key, parameters);
export function createGraphScopeControls(
  scope,
  { preferences = createGraphScopePreferences() } = {},
) {
  let clients = [],
    vaults = [],
    saved = null,
    deniedScope = null;
  const host = el("div", null, "graph-scope-controls"),
    defaults = el("p", null, "muted"),
    feedback = el("p"),
    status = el("p"),
    save = el("button", copy("save")),
    clear = el("button", copy("clear"));
  host.dataset.draft = "ignore";
  host.dataset.graphScopeControls = "true";
  save.type = clear.type = "button";
  save.dataset.scopeDefault = "save";
  clear.dataset.scopeDefault = "clear";
  status.setAttribute("role", "status");
  feedback.setAttribute("role", "status");
  host.append(
    defaults,
    el("p", copy("local_only"), "muted"),
    save,
    clear,
    feedback,
    status,
  );
  function name(value) {
    return value
      ? `${clients.find((client) => client.id === value.client_id)?.name || ""} / ${vaults.find((vault) => vault.id === value.vault_id)?.name || ""}`
      : "";
  }
  function paint() {
    const allowed = authorizedGraphScopes(clients, vaults);
    const validSaved = allowed.some((value) => sameGraphScope(value, saved));
    setText(
      defaults,
      saved
        ? validSaved
          ? copy("default", { p0: name(saved) })
          : copy("default_unavailable")
        : copy("no_default"),
    );
    save.disabled =
      sameGraphScope(deniedScope, scope()) ||
      !allowed.some((value) => sameGraphScope(value, scope()));
  }
  function readSaved() {
    const result = preferences.read();
    saved = result.scope;
    if (result.problem) setText(feedback, copy(result.problem));
    paint();
    return result;
  }
  save.addEventListener("click", () => {
    if (save.disabled) return;
    const result = preferences.save(scope(), clients, vaults);
    if (result.ok) {
      readSaved();
      setText(feedback, copy("saved"));
    } else setText(feedback, copy(result.problem));
  });
  clear.addEventListener("click", () => {
    const result = preferences.clear();
    if (result.ok) {
      saved = null;
      paint();
      setText(feedback, copy("cleared"));
    } else setText(feedback, copy(result.problem));
  });
  return {
    host,
    catalog(nextClients, nextVaults, current, entering) {
      clients = nextClients;
      vaults = nextVaults;
      setText(feedback, "");
      const stored = readSaved();
      const result = resolveGraphScope(clients, vaults, {
        saved,
        current,
        entering,
      });
      if (result.notice) setText(feedback, copy(result.notice));
      else if (stored.problem) setText(feedback, copy(stored.problem));
      return result.scope;
    },
    changed: paint,
    readState({ state, key, error }) {
      host.setAttribute("aria-busy", String(state === "loading"));
      if (state === "failed" && [401, 403].includes(error?.status)) {
        deniedScope = { ...scope() };
        paint();
      }
      if (state === "ready") {
        deniedScope = null;
        paint();
      }
      if (state === "idle") {
        setText(status, "");
        return;
      }
      if (state === "loading" && key.startsWith("catalog:")) {
        setText(status, copy("loading_catalog"));
        return;
      }
      if (!scope().client_id || !scope().vault_id) {
        setText(status, copy("no_scope"));
        return;
      }
      const label = name(scope());
      setText(
        status,
        copy(
          state === "loading"
            ? key.startsWith("catalog:")
              ? "loading_catalog"
              : "loading"
            : state,
          {
            p0: label,
            p1: sourceText(
              String(error?.message || "")
                .trim()
                .replace(/[。.!！？?]+$/u, ""),
            ),
          },
        ),
      );
    },
    reset() {
      clients = [];
      vaults = [];
      setText(status, "");
      setText(feedback, "");
      host.setAttribute("aria-busy", "false");
      paint();
    },
  };
}
