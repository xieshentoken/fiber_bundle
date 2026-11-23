import { el } from "./ui.js";
import { cloudText as t } from "./note_cloud_copy.js";
import { joinText } from "./i18n/index.js";
const evidenceCodes = new Set([
  "index_not_current",
  "indexed_moc_unavailable",
  "pending_moc_file_plan",
  "indexed_file_differs_from_applied",
  "indexed_managed_identity",
  "indexed_note_link",
  "ledger_newer_than_indexed_applied_file",
  "indexed_evidence_absent",
]);
const stages = {
  draft: "artifactDraft",
  published: "artifactPublished",
  final: "artifactFinal",
};
const reviews = {
  deferred: "reviewDeferred",
  pending: "reviewPending",
  unreviewed: "reviewPending",
  reviewed: "reviewReviewed",
  approved: "reviewApproved",
  rejected: "reviewRejected",
};

// Presentation of evidence never upgrades a saved membership to a synced file.
export const cloudNodeLabel = (node) =>
  node.metadata_visible ? node.label : t("unknownTarget");
export const cloudNodeKind = (node) => t("kind_" + node.kind);
export function cloudNodeFacts(node) {
  const host = el("div", null, "nc-entity-facts");
  if (node.kind === "expert") {
    host.append(
      el(
        "p",
        t("expertEntry", { kind: t("entry_" + node.entry_kind) }),
        "nc-muted",
      ),
    );
    host.append(
      el(
        "p",
        t("expertReview", {
          stage: Object.hasOwn(stages, node.artifact_stage)
            ? t(stages[node.artifact_stage])
            : node.artifact_stage,
          review: Object.hasOwn(reviews, node.review_state)
            ? t(reviews[node.review_state])
            : node.review_state,
          verified: t(node.knowledge_verified ? "verified" : "unverified"),
        }),
        "nc-muted",
      ),
    );
  }
  if (node.kind === "unresolved") {
    host.append(
      el(
        "p",
        t(
          node.reason === "publication_missing_confirmed"
            ? "publicationMissingConfirmed"
            : node.reason === "identity_not_indexed"
              ? "identityNotIndexed"
              : "unavailableTarget",
        ),
        "nc-target-warning",
      ),
    );
    if (node.path)
      host.append(el("p", t("historicalPath", { path: node.path }), "nc-path"));
    host.append(el("p", t("retainedMembership"), "nc-muted"));
  } else if (node.path) host.append(el("p", node.path, "nc-path"));
  return host;
}
export function cloudEdgeFacts(edges) {
  const host = el("div", null, "nc-edge-facts");
  for (const edge of edges) {
    if (edge.kind === "explicit") {
      host.append(el("span", t("explicit"), "nc-relation-kind"));
      continue;
    }
    const value = edge.membership;
    const row = el("div", null, "nc-membership");
    row.append(
      el(
        "span",
        joinText(
          [
            t("savedMembership"),
            joinText(
              value.owners.map((owner) => t("owner_" + owner)),
              " / ",
            ),
            t(value.manual_locked ? "locked" : "notLocked"),
          ],
          " · ",
        ),
        "nc-relation-kind",
      ),
    );
    row.append(el("span", t("sync_" + value.file_sync), "nc-sync-state"));
    row.append(
      el(
        "span",
        t(
          "evidence_" +
            (evidenceCodes.has(value.evidence)
              ? value.evidence
              : "unavailable"),
        ),
        "nc-sync-state",
      ),
    );
    // Fixed enum, never source text or a caller-supplied HTML fragment.
    row.dataset.fileSync = value.file_sync;
    row.dataset.membershipEvidence = value.evidence;
    host.append(row);
  }
  return host;
}
