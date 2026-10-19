// One bounded GET at a time; session/scope changes cancel only home reads.
export function createHomeData(read, now = Date.now) {
  let generation = 0,
    scope = "",
    active = null,
    pumping = false;
  const cache = new Map(),
    pending = new Map(),
    queue = [];
  function cancel() {
    generation++;
    active?.abort();
    pending.clear();
    for (const job of queue.splice(0)) {
      pending.delete(job.key);
      job.resolve(null);
    }
  }
  function setScope(next) {
    if (next === scope) return false;
    cancel();
    scope = next;
    cache.clear();
    return true;
  }
  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      while (queue.length) {
        const job = queue.shift(),
          controller = new AbortController();
        active = controller;
        const previous = cache.get(job.key);
        const record = { ...previous, scope, loading: true, error: null };
        try {
          const value = await read(job.name, job.params, controller.signal);
          if (job.generation === generation && !controller.signal.aborted) {
            Object.assign(record, { value, lastSuccess: now(), error: null });
          }
        } catch (error) {
          if (job.generation === generation && !controller.signal.aborted)
            record.error = {
              status: error.status || 0,
              code: error.code || "network",
              message: error.message,
            };
        }
        record.loading = false;
        record.observed = now();
        if (pending.get(job.key) === job.promise) pending.delete(job.key);
        if (job.generation === generation && !controller.signal.aborted) {
          cache.set(job.key, record);
          job.resolve(record);
        } else job.resolve(null);
        active = null;
      }
    } finally {
      pumping = false;
    }
  }
  function get(name, params = {}, { ttl = 60000, force = false } = {}) {
    const ordered = Object.fromEntries(
      Object.entries(params).sort(([a], [b]) => a.localeCompare(b)),
    );
    const key = JSON.stringify([scope, name, ordered]),
      previous = cache.get(key);
    if (pending.has(key)) return pending.get(key);
    if (!force && previous && now() - previous.observed < ttl)
      return Promise.resolve(previous);
    const promise = new Promise((resolve) => {
      queue.push({ key, name, params: ordered, generation, resolve });
    });
    queue[queue.length - 1].promise = promise;
    pending.set(key, promise);
    void pump();
    return promise;
  }
  return {
    get,
    setScope,
    cancel,
    clear() {
      cancel();
      cache.clear();
      scope = "";
    },
  };
}
