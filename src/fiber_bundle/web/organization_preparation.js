// One explicit, bounded pass. Never poll folders or retry an uncertain download.
export async function prepareWaitingSources(job, fixed, api, options = {}) {
  const paths = [...new Set(job.waiting_sources || [])],
    results = new Map(
      (options.previous || [])
        .filter((r) => paths.includes(r.path))
        .map((r) => [r.path, r]),
    ),
    started = Date.now();
  const offset = options.offset || 0,
    ordered = paths.slice(offset).concat(paths.slice(0, offset));
  let checked = 0;
  for (const path of ordered.slice(0, 10)) {
    if (Date.now() - started > 25000 || options.active?.() === false) break;
    try {
      let status = await api.cloudFile({ ...fixed, path });
      if (["not_requested", "error"].includes(status.state))
        status = await api.cloudFile({
          ...fixed,
          path,
          action: "download",
          expected: status.expected,
        });
      if (status.state === "available")
        status = await api.cloudFile({
          ...fixed,
          path,
          action: "recheck",
          expected: status.expected,
        });
      results.set(path, { path, state: status.state });
    } catch (error) {
      results.set(path, { path, state: "error", code: error.code });
    }
    checked++;
    options.progress?.(results.size, paths.length);
  }
  const ready = [...results.values()].filter(
    (r) => r.state === "available",
  ).length;
  // A user requested this continuation. Closing/changing the view withdraws it.
  const resume =
    paths.length > 0 &&
    ready === paths.length &&
    !job.blocking_task &&
    options.active?.() !== false;
  if (resume) await api.maintenanceAction(job.id, "resume", fixed);
  return {
    results: [...results.values()],
    ready,
    total: paths.length,
    resumed: resume,
    nextOffset: paths.length ? (offset + checked) % paths.length : 0,
  };
}
