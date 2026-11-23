// Page defaults are client-local IDs, never permissions or backend query defaults.
export const GRAPH_SCOPE_KEY = "fiber.graph-scope.v1";
const id = (value) =>
  typeof value === "string" &&
  /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/.test(value);
export const sameGraphScope = (a, b) =>
  !!a && !!b && a.client_id === b.client_id && a.vault_id === b.vault_id;
export function authorizedGraphScopes(clients, vaults) {
  return clients
    .filter((client) => client.active)
    .flatMap((client) =>
      vaults
        .filter(
          (vault) =>
            vault.status === "active" &&
            (!vault.attachment_state ||
              vault.attachment_state === "attached") &&
            client.grants?.some(
              (grant) => grant.vault_id === vault.id && grant.can_query,
            ),
        )
        .map((vault) => ({ client_id: client.id, vault_id: vault.id })),
    );
}
export function resolveGraphScope(
  clients,
  vaults,
  { saved, current, entering = false } = {},
) {
  const allowed = authorizedGraphScopes(clients, vaults);
  const desired = entering ? saved : current;
  const found = allowed.find((scope) => sameGraphScope(scope, desired));
  return {
    scope: found || allowed[0] || null,
    notice: !allowed.length
      ? "no_scope"
      : desired && !found
        ? entering
          ? "invalid_default"
          : "invalid_current"
        : null,
  };
}
function localStorage() {
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}
export function createGraphScopePreferences(storage = localStorage()) {
  return {
    read() {
      try {
        if (!storage) return { scope: null, problem: "storage_unavailable" };
        const raw = storage.getItem(GRAPH_SCOPE_KEY);
        if (raw === null) return { scope: null, problem: null };
        let value;
        try {
          value = JSON.parse(raw);
        } catch {
          return { scope: null, problem: "storage_corrupt" };
        }
        if (
          !value ||
          value.version !== 1 ||
          !id(value.client_id) ||
          !id(value.vault_id)
        )
          return { scope: null, problem: "storage_corrupt" };
        return {
          scope: { client_id: value.client_id, vault_id: value.vault_id },
          problem: null,
        };
      } catch {
        return { scope: null, problem: "storage_unavailable" };
      }
    },
    save(scope, clients, vaults) {
      if (
        !id(scope?.client_id) ||
        !id(scope?.vault_id) ||
        !authorizedGraphScopes(clients, vaults).some((allowed) =>
          sameGraphScope(scope, allowed),
        )
      )
        return { ok: false, problem: "invalid_current" };
      try {
        if (!storage) throw Error("storage_unavailable");
        storage.setItem(
          GRAPH_SCOPE_KEY,
          JSON.stringify({
            version: 1,
            client_id: scope.client_id,
            vault_id: scope.vault_id,
          }),
        );
        return { ok: true };
      } catch {
        return { ok: false, problem: "save_failed" };
      }
    },
    clear() {
      try {
        if (!storage) throw Error("storage_unavailable");
        storage.removeItem(GRAPH_SCOPE_KEY);
        return { ok: true };
      } catch {
        return { ok: false, problem: "clear_failed" };
      }
    },
  };
}
