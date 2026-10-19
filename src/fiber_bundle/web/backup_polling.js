// Status reads are serial and page-scoped. They never submit or retry a backup.
export function createBackupPolling({
  read,
  onValue,
  visible,
  onError = () => {},
  schedule = setTimeout,
  cancel = clearTimeout,
}) {
  let timer = null,
    generation = 0,
    latest = null;
  function stop() {
    generation++;
    if (timer !== null) cancel(timer);
    timer = null;
  }
  function start(value) {
    stop();
    latest = value;
    const owned = generation;
    function next() {
      if (owned !== generation) return;
      const busy = latest.jobs.some((job) =>
        ["queued", "running", "deleting"].includes(job.state),
      );
      timer = schedule(tick, busy ? 2000 : 10000);
    }
    async function tick() {
      timer = null;
      if (owned !== generation) return;
      if (!visible()) {
        next();
        return;
      }
      try {
        const status = await read();
        if (owned !== generation) return;
        latest = status;
        onValue(status);
      } catch (error) {
        if (owned === generation) onError(error);
      }
      next();
    }
    next();
  }
  return { start, stop };
}
