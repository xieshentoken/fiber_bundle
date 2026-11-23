import { joinText, msg } from "./i18n/index.js";

const failureKeys = {
  intake_article_image_rate_limited: "intake.article_images.rate_limited",
  intake_article_image_http_unavailable:
    "intake.article_images.http_unavailable",
  intake_article_image_http_error: "intake.article_images.http_error",
  intake_article_image_incomplete_response: "intake.article_images.incomplete",
  intake_article_image_size_invalid: "intake.article_images.size",
  intake_article_image_total_limit: "intake.article_images.total_size",
  intake_article_image_response_invalid: "intake.article_images.format",
  intake_article_image_dns_failed: "intake.article_images.dns",
  intake_article_image_certificate_invalid: "intake.article_images.certificate",
  intake_article_image_tls_interrupted: "intake.article_images.connection",
  intake_article_image_tls_failed: "intake.article_images.tls",
  intake_article_image_timeout: "intake.article_images.timeout",
  intake_article_image_connection_interrupted:
    "intake.article_images.connection",
  intake_article_image_source_invalid: "intake.article_images.source",
  intake_auxiliary_host_not_allowed: "intake.article_images.source",
  intake_article_image_connection_failed: "intake.article_images.connection",
  intake_content_incomplete: "intake.article_images.format",
  intake_fetch_timeout: "intake.article_images.timeout",
  intake_download_budget_exhausted: "intake.article_images.budget",
};

export function articleImageStatus(link) {
  const progress = link.article_image_progress;
  if (!progress) return null;
  const { state, total, complete, failure_code: reason } = progress;
  if (
    !["downloading", "pending", "complete"].includes(state) ||
    !Number.isSafeInteger(total) ||
    !Number.isSafeInteger(complete) ||
    total <= 0 ||
    complete < 0 ||
    complete > total ||
    (state === "complete" && complete !== total) ||
    (state === "pending" && complete === total)
  )
    return null;

  const published =
    link.state === "published" || link.publication?.state === "published";
  if (published && state !== "complete") return null;
  const waiting = state === "pending";
  const active = state === "downloading";
  // Older workers marked generic connection failures as terminal. Allow a
  // user to retry the original download after updating the worker.
  const canResume =
    progress.can_resume !== false ||
    reason === "intake_article_image_connection_failed";
  const resume =
    waiting &&
    canResume &&
    !link.requires_new_model_attempt &&
    ["needs_review", "link_only"].includes(link.state);
  const phase = resume ? "waiting" : waiting ? "interrupted" : state;
  const status = msg(`intake.article_images.${phase}`, { complete, total });
  const failure = Object.hasOwn(failureKeys, reason)
    ? msg(failureKeys[reason])
    : msg("intake.article_images.failure");
  return {
    text: waiting ? joinText([status, failure], " · ") : status,
    showAsPrimary: !published && (waiting || active),
    blockRetry: published || active || (waiting && !resume),
    retryLabel: resume ? msg("intake.article_images.resume") : null,
  };
}
