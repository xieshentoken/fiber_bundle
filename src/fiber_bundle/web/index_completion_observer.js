// Observe one explicit index request. Only lightweight status reads may repeat.
export function createIndexCompletionObserver(
  read,
  scope,
  complete,
  { interval = 5000, duration = 900000 } = {},
) {
  let epoch = 0,
    timer = null,
    controller = null,
    selected = null,
    deadline = 0;
  const stop = () => {
    epoch++;
    clearTimeout(timer);
    controller?.abort();
    controller = null;
    selected = null;
  };
  function start(fixed) {
    stop();
    selected = JSON.stringify(fixed);
    deadline = Date.now() + duration;
    const mine = epoch,
      key = selected;
    async function poll() {
      if (mine !== epoch) return;
      if (key !== JSON.stringify(scope()) || Date.now() >= deadline) {
        stop();
        return;
      }
      controller = new AbortController();
      try {
        const value = await read({ signal: controller.signal });
        if (mine !== epoch || key !== JSON.stringify(scope())) return;
        const row = value.vaults.find((v) => v.vault_id === fixed.vault_id);
        if (row && ["ready", "needs_review"].includes(row.state)) {
          stop();
          await complete(row);
          return;
        }
      } catch {
        // An uncertain read never repeats the update POST or declares success.
      } finally {
        if (mine === epoch && key === selected)
          timer = setTimeout(poll, interval);
      }
    }
    void poll();
  }
  return { start, stop };
}
