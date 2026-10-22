// Share one read per selected scope; mutation requests never pass through this gate.
export function scopedRead(read) {
  let active;
  return (options = {}) => {
    const key = options.key;
    if (active?.key === key) return active.promise;
    const promise = Promise.resolve().then(() => read(options));
    active = { key, promise };
    promise
      .finally(() => {
        if (active?.promise === promise) active = null;
      })
      .catch(() => {});
    return promise;
  };
}
