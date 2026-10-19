// Read-only, bounded observation. Epoch cancellation also discards in-flight responses.
export function createTaskObserver({
  read,
  onUpdate,
  onStop,
  interval = 1500,
  timeout = 180000,
  now = Date.now,
  schedule = setTimeout,
  cancel = clearTimeout,
}) {
  let generation = 0,
    timer = null,
    expiry = null;
  function stop() {
    generation++;
    if (timer !== null) cancel(timer);
    if (expiry !== null) cancel(expiry);
    timer = null;
    expiry = null;
  }
  return {
    stop,
    start(tasks) {
      stop();
      const watched = tasks
        .filter((t) => ["running", "launch_requested"].includes(t.state))
        .map((t) => t.id);
      if (!watched.length) return;
      const fixed = generation,
        deadline = now() + timeout;
      expiry = schedule(() => {
        if (fixed !== generation) return;
        stop();
        onStop("timeout");
      }, timeout);
      async function poll() {
        if (fixed !== generation) return;
        if (now() >= deadline) {
          stop();
          onStop("timeout");
          return;
        }
        try {
          const current = await read();
          if (fixed !== generation) return;
          if (now() >= deadline) {
            stop();
            onStop("timeout");
            return;
          }
          const rows = watched.map((id) => current.find((t) => t.id === id));
          await onUpdate(current, watched);
          if (fixed !== generation) return;
          if (
            rows.every(
              (t) =>
                !t ||
                !["running", "queued", "launch_requested"].includes(t.state),
            )
          ) {
            stop();
            onStop(rows.some((t) => !t) ? "unavailable" : "terminal");
            return;
          }
          timer = schedule(poll, Math.min(interval, deadline - now()));
        } catch (error) {
          if (fixed !== generation) return;
          stop();
          onStop("error", error);
        }
      }
      timer = schedule(poll, interval);
    },
  };
}
