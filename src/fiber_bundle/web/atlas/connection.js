// Only this serialized health monitor owns the frame's connectivity indicator.
export function createConnectionMonitor(check, publish, interval = 15000) {
  let active = false,
    epoch = 0,
    failures = 0,
    state = "connecting",
    timer = null,
    running = null,
    controller = null;
  function show(next) {
    if (next !== state) {
      state = next;
      publish(next);
    }
  }
  function refresh() {
    if (running) return running;
    const version = epoch;
    const request = new AbortController();
    controller = request;
    const pending = Promise.resolve()
      .then(() => check({ signal: request.signal }))
      .then((result) => {
        if (version !== epoch) return;
        if (result?.status !== "ok") throw new Error("invalid_health");
        failures = 0;
        show("connected");
      })
      .catch(() => {
        if (version !== epoch) return;
        failures++;
        show(failures >= 2 ? "disconnected" : "checking");
      })
      .finally(() => {
        if (version !== epoch || running !== pending) return;
        running = null;
        controller = null;
        clearTimeout(timer);
        if (active) timer = setTimeout(refresh, interval);
      });
    running = pending;
    return pending;
  }
  return {
    refresh,
    start() {
      if (active) return;
      active = true;
      void refresh();
    },
    stop() {
      active = false;
      epoch++;
      clearTimeout(timer);
      controller?.abort();
      controller = null;
      running = null;
    },
  };
}
