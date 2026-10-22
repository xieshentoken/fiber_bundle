import { visibleVault } from "./vault_visibility.js";
// Short-lived shared reads (vaults, clients, components) used by several views.
// Any mutation can invalidate them; a session change clears everything.
export function createStore(api, now = Date.now) {
  const entries = new Map();
  const readers = {
    vaults: async () => (await api.vaults()).filter(visibleVault),
    clients: () => api.clients(),
    components: () => api.components(),
    agents: () => api.agents.list(),
  };
  function get(name, { ttl = 15000, force = false } = {}) {
    const entry = entries.get(name);
    if (entry && (entry.pending || (!force && now() - entry.at < ttl)))
      return entry.pending || Promise.resolve(entry.value);
    const pending = readers[name]().then(
      (value) => {
        if (entries.get(name)?.pending === pending)
          entries.set(name, { value, at: now(), pending: null });
        return value;
      },
      (error) => {
        if (entries.get(name)?.pending === pending) entries.delete(name);
        throw error;
      },
    );
    entries.set(name, { ...entry, pending });
    return pending;
  }
  return {
    vaults: (options) => get("vaults", options),
    clients: (options) => get("clients", options),
    components: (options) => get("components", options),
    agents: (options) => get("agents", options),
    invalidate(...names) {
      for (const name of names.length ? names : [...entries.keys()])
        entries.delete(name);
    },
    clear() {
      entries.clear();
    },
  };
}
// Active clients with all of the listed grants on the vault (or on any vault).
export function clientsWith(clients, grants, vaultId) {
  return clients.filter(
    (client) =>
      client.active &&
      client.grants.some(
        (grant) =>
          (!vaultId || grant.vault_id === vaultId) &&
          grants.every((name) => grant["can_" + name]),
      ),
  );
}
export function vaultsFor(client, vaults, grants) {
  return vaults.filter(
    (vault) =>
      vault.status === "active" &&
      client?.grants.some(
        (grant) =>
          grant.vault_id === vault.id &&
          grants.every((name) => grant["can_" + name]),
      ),
  );
}
