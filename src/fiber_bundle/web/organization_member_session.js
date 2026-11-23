// Manual drafts and receipts live outside catalog cards, in this view's scope.
const scopeKey = (value) =>
  JSON.stringify([value?.client_id || "", value?.vault_id || ""]);

function receipt(batch, value) {
  const rows = Array.isArray(value?.results) ? value.results : [];
  const expected = new Set(batch.targets.map((target) => target.moc_id));
  const unexpected = rows.some((row) => !expected.has(row?.moc_id));
  return batch.targets.map((target) => {
    const matches = rows.filter((row) => row?.moc_id === target.moc_id);
    const row = matches[0];
    if (
      unexpected ||
      matches.length !== 1 ||
      typeof row?.completed !== "boolean"
    )
      return { ...target, state: "unknown", error: "manual_response_invalid" };
    return {
      ...target,
      state: row.completed ? "completed" : "incomplete",
      error: row.error || "organization_manual_write_failed",
    };
  });
}

export function createMemberSession({
  scope,
  submit,
  refresh,
  changed,
  denied,
}) {
  let key = "",
    generation = 0,
    entries = new Map(),
    latest = null;
  function current(entry) {
    return (
      entry.generation === generation &&
      key === scopeKey(scope()) &&
      entries.get(entry.id) === entry
    );
  }
  function publish() {
    for (const entry of entries.values()) entry.update?.();
    changed?.(
      latest,
      [...entries.values()]
        .map((entry) => entry.lastResult)
        .filter(
          (result) =>
            result && result !== latest && result.phase !== "completed",
        ),
    );
  }
  function clear() {
    generation++;
    entries = new Map();
    latest = null;
    key = "";
    publish();
  }
  function activate(fixed) {
    const next = scopeKey(fixed);
    if (key === next) return;
    clear();
    key = next;
  }
  const busy = () => [...entries.values()].some((entry) => entry.pending);
  return {
    clear,
    activate,
    busy,
    dirty: () => [...entries.values()].some((entry) => entry.selected.size),
    entry(item, mocs) {
      let entry = entries.get(item.id);
      if (!entry) {
        entry = {
          id: item.id,
          generation,
          selected: new Set(),
          search: "",
          open: false,
          pending: false,
          unknown: false,
        };
        entries.set(item.id, entry);
      }
      entry.item = { id: item.id, title: item.title, path: item.path };
      entry.mocs = mocs;
      return entry;
    },
    editable: (entry) => current(entry) && !busy() && !entry.unknown,
    update(entry, work) {
      if (!current(entry) || busy() || entry.unknown) return;
      work(entry);
      publish();
    },
    clearSelection(entry) {
      if (!current(entry) || busy()) return;
      entry.selected.clear();
      entry.unknown = false;
      publish();
    },
    async apply(entry, action) {
      // A detached card must never submit to a previous identity or vault.
      if (!current(entry) || busy() || entry.unknown) return;
      if (!entry.selected.size) throw new Error("请先勾选一个或多个 MOC。");
      const fixed = { ...scope() };
      const batch = {
        entity: { ...entry.item },
        action,
        phase: "pending",
        targets: [...entry.selected].map((id) => ({
          moc_id: id,
          title: entry.mocs.find((moc) => moc.id === id)?.title || id,
        })),
      };
      entry.pending = true;
      latest = entry.lastResult = batch;
      publish();
      try {
        const value = await submit({
          ...fixed,
          entity_id: entry.id,
          moc_ids: batch.targets.map((target) => target.moc_id),
          action,
        });
        if (!current(entry)) return;
        batch.results = receipt(batch, value);
        batch.resolved = !!value?.resolved_items;
      } catch (error) {
        if (!current(entry)) return;
        if ([401, 403, 404].includes(error.status)) {
          clear();
          denied?.(error);
          return;
        }
        // These codes are raised while validating the catalog/targets, before
        // this batch applies members. Other HTTP failures can follow a write.
        const rejected = [
          "organization_catalog_pending",
          "organization_member_not_visible",
        ].includes(error.code);
        batch.results = batch.targets.map((target) => ({
          ...target,
          state: rejected ? "incomplete" : "unknown",
          error: error.code || "network",
        }));
      } finally {
        if (current(entry)) entry.pending = false;
      }
      if (!current(entry)) return;
      entry.unknown = batch.results.some((row) => row.state === "unknown");
      const completed = batch.results.filter(
        (row) => row.state === "completed",
      );
      batch.phase = entry.unknown
        ? "unknown"
        : completed.length === batch.results.length
          ? "completed"
          : completed.length
            ? "partial"
            : "incomplete";
      for (const row of completed) entry.selected.delete(row.moc_id);
      // Commit and display receipts before any catalog read can replace a card.
      publish();
      if (!current(entry)) return;
      try {
        await refresh();
      } catch {
        // Catalog failure never rewrites a write receipt or retries a mutation.
      }
      return batch;
    },
  };
}
