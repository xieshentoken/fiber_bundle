// A single bounded delay for an explicitly busy read; never used for mutations.
export function waitForReadRetry(signal, milliseconds = 1000) {
  if (signal?.aborted) return Promise.resolve(false);
  return new Promise((resolve) => {
    let timer;
    function finish(value) {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      resolve(value);
    }
    function abort() {
      finish(false);
    }
    timer = setTimeout(() => finish(true), milliseconds);
    signal?.addEventListener("abort", abort, { once: true });
  });
}
