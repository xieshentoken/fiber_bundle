// Only authoritative publication is completion; raw archival and extraction are not.
export function isInboxComplete(item) {
  const intake = item.intake;
  if (intake?.source_failures?.length || intake?.reason) return false;
  const jobs = intake?.jobs || [];
  if (jobs.some((job) => !["complete", "cancelled"].includes(job.state)))
    return false;
  if (jobs.some((job) => job.missing_images > 0)) return false;
  if (intake?.links?.some((link) => link.published !== true)) return false;
  if (
    item.automatic_state &&
    !["complete", "cancelled"].includes(item.automatic_state)
  )
    return false;
  if (
    item.automatic?.state &&
    !["complete", "cancelled"].includes(item.automatic.state)
  )
    return false;
  return (
    item.publication_state === "published" ||
    item.publication?.state === "published"
  );
}
