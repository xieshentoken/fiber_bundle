// One disposable deadline check per visible batch page. Expiry never freezes a batch.
export function createBatchCountdown(
  refresh,
  onError,
  { now = Date.now, schedule = setTimeout, cancel = clearTimeout } = {},
) {
  let batches = [],
    visible = false,
    timer = null,
    generation = 0;
  const checked = new Set();
  const key = (batch) => `${batch.id}:${batch.freeze_at}`;
  function stop() {
    generation++;
    if (timer !== null) cancel(timer);
    timer = null;
  }
  function arm() {
    stop();
    if (!visible) return;
    const pending = batches.filter(
      (batch) =>
        batch.state === "buffering" &&
        Number.isFinite(batch.freeze_at) &&
        batch.freeze_at * 1000 > now() &&
        !checked.has(key(batch)),
    );
    if (!pending.length) return;
    const deadline = Math.min(
      ...pending.map((batch) => batch.freeze_at * 1000),
    );
    const current = generation;
    timer = schedule(
      async () => {
        timer = null;
        if (!visible || current !== generation) return;
        const due = pending.filter((batch) => batch.freeze_at * 1000 <= now());
        if (!due.length) {
          arm();
          return;
        }
        due.forEach((batch) => checked.add(key(batch)));
        try {
          await refresh();
        } catch (error) {
          if (visible && current === generation) onError(error);
        } finally {
          if (visible && current === generation) arm();
        }
      },
      Math.ceil(deadline - now()) + 50,
    );
  }
  return {
    update(items) {
      batches = items;
      const remaining = new Set(items.map(key));
      for (const item of checked)
        if (!remaining.has(item)) checked.delete(item);
      arm();
    },
    setVisible(value) {
      visible = value;
      arm();
    },
    clear() {
      visible = false;
      batches = [];
      checked.clear();
      stop();
    },
  };
}
