// Preflight failures are distinct from a task that exists and stopped while applying.
import { msg, sourceText, joinText } from "./i18n/index.js";
import { el } from "./ui.js";
import { uiText } from "./i18n/index.js";

const catalogErrors = new Set([
  "organization_catalog_changed",
  "organization_catalog_pending",
  "organization_moc_unavailable",
]);
export const catalogPreflightFailed = (error) => catalogErrors.has(error?.code);

export function catalogIssueMessage(issue) {
  const key =
    {
      organization_directory_names_changed: "names",
      organization_directory_changed_during_read: "changing",
      organization_catalog_observation_changed: "changing",
      organization_catalog_observation_pending: "pending",
    }[issue.reason] || "metadata";
  return msg("organization.recovery." + key);
}

export function appendCatalogFailure(host, error) {
  if (!catalogPreflightFailed(error)) return false;
  host.append(el("p", msg("organization.recovery.not_created"), "task-impact"));
  for (const issue of error.issues || []) {
    host.append(
      el(
        "p",
        joinText([
          msg("organization.recovery.directory"),
          " · ",
          issue.path
            ? sourceText(issue.path)
            : msg("organization.recovery.root"),
        ]),
      ),
      el("p", catalogIssueMessage(issue), "muted"),
    );
  }
  return true;
}

export function appendRetainedMembers(host, issues) {
  if (!Array.isArray(issues) || !issues.length) return false;
  host.append(el("p", msg("organization.recovery.retained"), "task-impact"));
  for (const issue of issues)
    host.append(el("p", sourceText(issue.path || issue.identity), "muted"));
  return true;
}

// A catalog read can fail while a persisted task still has valid progress.
export function appendCatalogObservation(host, error) {
  if (!error) return;
  host.append(el("p", uiText(error.message), "task-impact"));
  for (const issue of error.issues || [])
    host.append(
      el(
        "p",
        joinText([
          sourceText(issue.path || "."),
          " · ",
          catalogIssueMessage(issue),
        ]),
        "muted",
      ),
    );
}
