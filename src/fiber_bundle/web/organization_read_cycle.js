// Catalog reads follow user actions or a completed application, never a retry timer.
import { effectiveIndexState, mergeProgress } from "./organization_progress.js";

export function organizationWorking(result) {
  return (
    result.package_scan?.state === "queued" ||
    result.jobs.some(
      (job) =>
        ["queued", "running", "cancelling"].includes(job.state) ||
        (job.index_state &&
          ["pending", "building", "waiting"].includes(
            effectiveIndexState(job),
          )) ||
        (job.index_state === "pending" && !job.index_observation),
    )
  );
}

function applied(previous, current) {
  return current.jobs.some(
    (job) =>
      ["completed", "partial", "cancelled"].includes(job.state) &&
      job.index_state !== "pending" &&
      previous?.jobs.some(
        (prior) =>
          prior.id === job.id &&
          (prior.state !== job.state || prior.index_state !== job.index_state),
      ),
  );
}

export async function readOrganizationCycle(
  api,
  fixed,
  previous,
  { automatic = false, offset = 0, query = "", exceptions = true } = {},
) {
  let data = previous;
  if (automatic) {
    const ledger = await api.progress(fixed);
    data = previous ? mergeProgress(previous, ledger) : ledger;
    if (!applied(previous, data)) return { data, catalog: null, error: null };
  }
  try {
    return {
      data: await api.list(fixed, offset, query, exceptions),
      catalog: true,
      error: null,
    };
  } catch (error) {
    if ([401, 403, 404].includes(error.status)) throw error;
    if (!automatic) {
      const ledger = await api.progress(fixed);
      data = previous ? mergeProgress(previous, ledger) : ledger;
    }
    return { data, catalog: false, error };
  }
}

export const progressRetryDelay = (attempt) =>
  attempt < 3 ? 30000 * 2 ** attempt : null;
