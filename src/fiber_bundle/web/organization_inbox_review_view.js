import { appendCatalogFailure } from "./organization_catalog_feedback.js";
// Plain-text evidence rendering: source text never enters an HTML parser.
import { el } from "./ui.js";
import { msg, sourceText, localizedDate } from "./i18n/index.js";
export const copy = (key, parameters) => msg("inbox_review." + key, parameters);

export function reviewButton(label, run, disabled = false) {
  const button = el("button", label);
  button.type = "button";
  button.disabled = disabled;
  button.addEventListener("click", run);
  return button;
}

export function appendReviewFailure(host, error, state = {}) {
  if (!error) return;
  if (appendCatalogFailure(host, error)) return;
  const detail = error.error_detail || error.diagnostic || {},
    reason = detail.reason || error.code || "unknown",
    readable = reasonKey(reason) || reasonKey(error.code);
  host.append(
    el("p", readable ? copy(readable) : copy("failed"), "task-failure"),
  );
  if (detail.path) host.append(el("code", sourceText(detail.path)));
  const next = nextStep(detail.next_step, reason);
  host.append(el("p", copy(next), "muted"));
  const technical = el("details", null, "technical-details");
  technical.open = !!state.diagnosticOpen;
  technical.addEventListener("toggle", () => {
    state.diagnosticOpen = technical.open;
  });
  technical.append(
    el("summary", copy("technical")),
    el(
      "pre",
      sourceText(
        JSON.stringify(
          {
            code: error.code,
            reason,
            path: detail.path,
            next_step: detail.next_step,
          },
          null,
          2,
        ),
      ),
    ),
  );
  host.append(technical);
}

export function appendInspection(host, inspection, state, choose) {
  host.append(
    el("p", copy("current_file", { p0: inspection.path })),
    el("p", copy("bytes", { p0: inspection.byte_count ?? copy("unknown") })),
    el(
      "p",
      copy("classification", {
        p0: copy(
          ["empty", "title_only", "content", "unreadable"].includes(
            inspection.classification,
          )
            ? inspection.classification
            : "unknown",
        ),
      }),
    ),
  );
  for (const publication of inspection.publication || [])
    host.append(
      el(
        "p",
        copy("publication", {
          p0: publication.publication_id,
          p1: ["committing", "published", "prepared"].includes(
            publication.state,
          )
            ? copy(publication.state)
            : publication.state,
        }),
        "muted",
      ),
    );
  for (const issue of inspection.issues || []) {
    host.append(el("p", reviewReason(issue.code), "task-impact"));
    if (issue.reason) {
      const technical = el("details", null, "technical-details");
      technical.append(
        el("summary", copy("technical")),
        el("pre", sourceText(issue.reason)),
      );
      host.append(technical);
    }
  }
  const text = inspection.text;
  if (text) {
    const body = el("pre", null, "inbox-review-text");
    body.setAttribute("aria-label", String(copy("current_text")));
    for (const line of text.lines || []) {
      const row = el("span", null, "inbox-review-line");
      row.append(
        el("span", String(line.number), "inbox-review-number"),
        el("code", sourceText(line.text)),
      );
      body.append(row);
    }
    host.append(body);
    if (text.truncated)
      host.append(
        el("p", copy("truncated", { p0: text.max_bytes }), "task-impact"),
      );
    if (!text.lines?.length) host.append(el("p", copy("no_text"), "muted"));
  }
  if (inspection.actions?.unlink_missing && inspection.links?.length) {
    host.append(el("p", copy("choose_links")));
    for (const link of inspection.links) {
      const label = el("label", null, "inbox-review-choice"),
        input = el("input");
      input.type = "checkbox";
      input.checked = state.selected.includes(link.id);
      input.disabled = !!state.busy;
      input.dataset.linkId = link.id;
      label.append(
        input,
        el("span", copy("link", { p0: link.line, p1: link.target })),
      );
      input.addEventListener("change", () => choose(link.id, input.checked));
      host.append(label);
      appendChanges(host, [
        { line: link.line, before: link.before, after: link.after },
      ]);
    }
  }
}

export function appendChanges(host, changes) {
  for (const change of changes || []) {
    const diff = el("div", null, "inbox-review-diff");
    diff.append(
      el("p", copy("line", { p0: change.line })),
      el("p", copy("before")),
      el("pre", sourceText(change.before)),
      el("p", copy("after")),
      el("pre", sourceText(change.after)),
    );
    host.append(diff);
  }
}

export function appendPlan(host, plan, state, apply) {
  const action = [
    "quarantine_empty",
    "unlink_missing",
    "restore_quarantine",
    "restore_links",
  ].includes(plan.action)
    ? plan.action
    : "unknown";
  host.append(
    el(
      "h4",
      copy(plan.state === "prepared" ? "plan" : "action", { p0: copy(action) }),
    ),
    el("code", sourceText(plan.path)),
    el("p", copy("operation", { p0: plan.operation_id })),
    el(
      "p",
      copy("state", {
        p0: copy(
          ["prepared", "applying", "reconcile", "completed"].includes(
            plan.state,
          )
            ? plan.state
            : "unknown",
        ),
      }),
    ),
  );
  appendChanges(host, plan.changes);
  if (plan.error) appendReviewFailure(host, { code: plan.error }, state);
  if (plan.action === "quarantine_empty")
    host.append(el("p", copy("quarantine_impact"), "task-impact"));
  if (plan.action === "unlink_missing")
    host.append(el("p", copy("unlink_impact"), "task-impact"));
  if (plan.action.startsWith("restore_"))
    host.append(el("p", copy("restore_impact"), "task-impact"));
  if (plan.state === "completed") {
    host.append(el("p", copy("completed_next"), "task-impact"));
    return;
  }
  if (plan.can_apply || plan.can_reconcile) {
    const consent = el("label", null, "inbox-review-choice"),
      input = el("input");
    input.type = "checkbox";
    input.checked = !!state.consent;
    input.disabled = !!state.busy;
    input.dataset.reviewConsent = plan.operation_id;
    consent.append(input, el("span", copy("consent")));
    const button = reviewButton(
      copy(plan.can_reconcile ? "confirm_reconcile" : "confirm_apply"),
      apply,
      !!state.busy || !state.consent,
    );
    input.addEventListener("change", () => {
      state.consent = input.checked;
      button.disabled = !!state.busy || !state.consent;
    });
    host.append(consent, button);
  }
}

const knownReasons = new Set([
  "inbox_review_restore_chain_invalid",
  "inbox_review_backup_changed",
  "inbox_review_diff_limit",
  "inbox_review_file_limit",
  "inbox_review_invalid_action",
  "inbox_review_move_conflict",
  "inbox_review_publication_quarantined",
  "inbox_review_stage_conflict",
  "inbox_review_stage_invalid",
  "inbox_review_publication_changed",
  "inbox_review_registration_changed",
  "inbox_review_already_restored",
  "inbox_review_concurrent_edit_restore_required",
  "inbox_review_restore_path_occupied",
  "inbox_review_restore_file_changed",
  "inbox_review_plan_changed",
  "inbox_review_stage_changed",
  "inbox_review_concurrent_edit_preserved",
  "inbox_review_file_changed",
  "inbox_review_link_selection_changed",
  "inbox_review_not_empty",
  "inbox_review_unreadable",
  "inbox_review_scope_or_directory_changed",
  "inbox_review_reconciliation_required",
  "inbox_review_restore_not_ready",
  "inbox_review_read_only",
  "inbox_review_package_not_supported",
  "inbox_review_publication_not_committed",
]);
const reasonAliases = {
  file_digest_changed: "inbox_review_file_changed",
  restore_path_occupied: "inbox_review_restore_path_occupied",
  restore_file_changed: "inbox_review_restore_file_changed",
  plan_changed: "inbox_review_plan_changed",
  stage_changed: "inbox_review_stage_changed",
  concurrent_edit_preserved: "inbox_review_concurrent_edit_preserved",
};
function reasonKey(code) {
  const full = String(code || ""),
    plain = full.replace(/^inbox_review_/, "");
  if (knownReasons.has(full)) return full;
  if (knownReasons.has("inbox_review_" + plain)) return "inbox_review_" + plain;
  return (
    reasonAliases[plain] ||
    (full === "timeout" || full === "network" ? full : null)
  );
}
function nextStep(step, reason) {
  const value = String(reason);
  if (value.includes("move_conflict")) return "next_move_conflict";
  if (value.includes("backup_changed")) return "next_preserve_edit";
  if (
    value.includes("restore_path_occupied") ||
    ["occupied", "resolve_conflict"].includes(step)
  )
    return "next_occupied";
  if (["restore_preview"].includes(step) || value.includes("concurrent_edit"))
    return "next_restore_preview";
  if (
    value.includes("restore_file_changed") ||
    value.includes("concurrent_edit_preserved")
  )
    return "next_preserve_edit";
  if (
    ["history", "reconcile"].includes(step) ||
    /reconcil|stage_changed|stage_invalid|stage_conflict|already_restored|restore_chain_invalid/.test(
      value,
    )
  )
    return "next_history";
  return "check_again";
}
export function reviewReason(code) {
  return reasonKey(code) ? copy(reasonKey(code)) : copy("failed");
}
export function appendHistoryIdentity(host, operation) {
  host.append(
    el(
      "p",
      copy("action", {
        p0: copy(
          [
            "quarantine_empty",
            "unlink_missing",
            "restore_quarantine",
            "restore_links",
          ].includes(operation.action)
            ? operation.action
            : "unknown",
        ),
      }),
    ),
  );
  if (Number.isFinite(operation.created_at))
    host.append(
      el(
        "p",
        localizedDate(operation.created_at * 1000, {
          dateStyle: "medium",
          timeStyle: "short",
        }),
      ),
    );
  if (operation.error)
    host.append(el("p", reviewReason(operation.error), "task-failure"));
}
