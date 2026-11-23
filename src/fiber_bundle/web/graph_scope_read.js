// Only the current read generation may publish success, failure, or completion.
// Returning to A after selecting B always starts a new A generation.
export function createGraphScopeReads(onState = () => {}) {
  let generation = 0,
    active = null;
  return {
    run(key, read) {
      if (active?.key === key) return active.promise;
      const turn = ++generation;
      const current = () => turn === generation;
      onState({ state: "loading", key });
      const promise = Promise.resolve()
        .then(() => (current() ? read({ current }) : undefined))
        .then((value) => {
          if (!current()) return;
          if (current())
            onState({
              state: value?.skipped
                ? "idle"
                : value?.partial
                  ? "partial"
                  : "ready",
              key,
              error: value?.error,
            });
          return value;
        })
        .catch((error) => {
          if (!current()) return;
          onState({ state: "failed", key, error });
          throw error;
        })
        .finally(() => {
          if (current()) active = null;
        });
      active = { key, promise };
      return promise;
    },
    pending(key) {
      return active?.key === key ? active.promise : null;
    },
    cancel() {
      generation++;
      active = null;
      onState({ state: "idle" });
    },
  };
}
