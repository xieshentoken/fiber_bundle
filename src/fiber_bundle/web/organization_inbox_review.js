// Scope-bound GUI workflow. Polling rebuilds views, while drafts retain user choices.
import { el } from "./ui.js";
import { disclosure } from "./view_sections.js";
import { previewSubmitted } from "./organization_workspace.js";
import {
  appendHistoryIdentity,
  appendInspection,
  appendPlan,
  appendReviewFailure,
  copy,
  reviewButton,
} from "./organization_inbox_review_view.js";

function store(draft, fixed) {
  const key = JSON.stringify([fixed.client_id, fixed.vault_id]);
  if (draft.inboxReview?.key !== key)
    draft.inboxReview = { key, panels: new Map(), history: { open: false } };
  return draft.inboxReview;
}
function entry(shared, key) {
  if (!shared.panels.has(key))
    shared.panels.set(key, { selected: [], open: false });
  return shared.panels.get(key);
}
async function run(state, stage, work) {
  if (state.busy) return;
  state.busy = stage;
  state.error = null;
  state.paint?.();
  try {
    await work();
  } catch (error) {
    state.error = error;
    state.consent = false;
    if ([401, 403, 404].includes(error.status)) {
      state.inspection = null;
      state.plan = null;
      state.operations = null;
      state.selected = [];
    }
    if (["applying_operation", "checking_operation"].includes(stage))
      state.plan = null;
  } finally {
    state.busy = null;
    state.paint?.();
  }
}
function invalidate(shared) {
  for (const state of shared.panels.values()) {
    if (state.plan?.state !== "completed") state.plan = null;
    state.consent = false;
    if (state.inspection) state.stale = true;
  }
  shared.history.loaded = false;
}
function newPlan(state, plan) {
  state.plan = plan;
  state.consent = false;
}

function appendRepreview(host, context, state) {
  if (context.draft.onOpenIndex)
    host.append(
      reviewButton(
        copy("open_index"),
        () => context.draft.onOpenIndex(context.fixed.vault_id),
        !!state.busy,
      ),
    );
  host.append(
    reviewButton(
      copy("repreview"),
      () =>
        run(state, "generating", async () => {
          await context.api.maintain({
            ...context.fixed,
            mode: "quick",
            unavailable_action: context.draft.unavailableAction || "moc_only",
            new_mocs: [],
          });
          previewSubmitted(context.draft);
          await context.refresh({ automatic: true, force: true });
        }),
      !!state.busy,
    ),
  );
}

function appendOperation(host, state, context, shared) {
  const plan = state.plan;
  if (!plan) return;
  appendPlan(host, plan, state, () =>
    run(
      state,
      plan.can_reconcile ? "checking_operation" : "applying_operation",
      async () => {
        // Failed writes are reviewed through current history, never replayed automatically.
        const result = await context.api.inboxReview.apply(plan.operation_id, {
          ...context.fixed,
          expected_plan_sha256: plan.plan_sha256,
        });
        invalidate(shared);
        newPlan(state, result);
        for (const panel of shared.panels.values())
          if (panel !== state) panel.paint?.();
        if (result.state === "completed") {
          state.inspection = null;
          shared.completed = result;
        }
      },
    ),
  );
  if (plan.can_restore)
    host.append(
      reviewButton(
        copy("preview_restore"),
        () =>
          run(state, "previewing", async () => {
            newPlan(
              state,
              await context.api.inboxReview.restorePreview(
                plan.operation_id,
                context.fixed,
              ),
            );
          }),
        !!state.busy,
      ),
    );
  if (plan.state === "completed") appendRepreview(host, context, state);
}

export function appendInboxReview(
  host,
  { job, path, fixed, api, refresh, draft },
) {
  if (!path || !api.inboxReview) return;
  const context = { fixed, api, refresh, draft },
    shared = store(draft, fixed),
    state = entry(shared, JSON.stringify([job.id, path])),
    details = disclosure("检查与处理"),
    body = el("div");
  details.classList.add("inbox-review");
  details.dataset.viewKey = `inbox-review:${job.id}:${path}`;
  details.open = state.open;
  details.append(body);
  function inspect() {
    return run(state, "reading", async () => {
      state.loaded = true;
      const result = await api.inboxReview.inspect({
        ...fixed,
        job_id: job.id,
        path,
      });
      state.selected = (
        state.inspection?.sha256 === result.sha256 ? state.selected : []
      ).filter((id) => result.links.some((link) => link.id === id));
      state.inspection = result;
      state.stale = false;
      state.loaded = true;
      newPlan(state, null);
    });
  }
  function preview(action) {
    return run(state, "previewing", async () => {
      newPlan(state, null);
      newPlan(
        state,
        await api.inboxReview.preview({
          ...fixed,
          job_id: job.id,
          path,
          action,
          ...(action === "unlink_missing"
            ? { link_ids: state.selected.slice() }
            : {}),
        }),
      );
    });
  }
  state.paint = () => {
    details.open = state.open;
    body.replaceChildren();
    if (state.busy) body.append(el("p", copy(state.busy), "task-impact"));
    appendReviewFailure(body, state.error, state);
    body.append(reviewButton(copy("refresh_file"), inspect, !!state.busy));
    if (state.inspection) {
      appendInspection(body, state.inspection, state, (id, checked) => {
        state.selected = state.selected.filter((value) => value !== id);
        if (checked) state.selected.push(id);
        newPlan(state, null);
        state.paint();
        Array.from(body.querySelectorAll("[data-link-id]"))
          .find((node) => node.dataset.linkId === id)
          ?.focus();
      });
      if (state.stale) body.append(el("p", copy("stale"), "task-impact"));
      else {
        if (state.inspection.actions?.quarantine_empty)
          body.append(
            reviewButton(
              copy("preview_quarantine"),
              () => preview("quarantine_empty"),
              !!state.busy,
            ),
          );
        if (state.inspection.actions?.unlink_missing)
          body.append(
            reviewButton(
              copy("preview_unlink"),
              () => preview("unlink_missing"),
              !!state.busy || !state.selected.length,
            ),
          );
      }
    }
    appendOperation(body, state, context, shared);
  };
  details.addEventListener("toggle", () => {
    state.open = details.open;
    if (state.open && !state.loaded && !state.busy) inspect();
  });
  state.paint();
  host.append(details);
}

export function appendInboxReviewHistory(host, context) {
  if (!context.api.inboxReview) return;
  const shared = store(context.draft, context.fixed),
    state = shared.history,
    details = disclosure("Inbox 处理历史与恢复"),
    body = el("div");
  details.classList.add("inbox-review");
  details.dataset.viewKey = "inbox-review-history";
  details.open = state.open;
  details.append(body);
  function load() {
    return run(state, "reading_history", async () => {
      const result = await context.api.inboxReview.history(context.fixed);
      state.operations = result.operations;
      state.truncated = result.truncated === true;
      state.loaded = true;
    });
  }
  state.paint = () => {
    details.open = state.open;
    body.replaceChildren(el("p", copy("history_intro"), "muted"));
    if (state.busy) body.append(el("p", copy(state.busy), "task-impact"));
    appendReviewFailure(body, state.error, state);
    body.append(reviewButton(copy("refresh_history"), load, !!state.busy));
    if (shared.completed) {
      body.append(el("p", copy("completed_next"), "task-impact"));
      appendRepreview(body, context, state);
    }
    if (state.truncated)
      body.append(el("p", copy("history_truncated"), "muted"));
    if (state.loaded && !state.operations?.length)
      body.append(el("p", copy("no_history"), "muted"));
    for (const operation of state.operations || []) {
      const row = disclosure(operation.path),
        record = entry(shared, `operation:${operation.operation_id}`),
        contents = el("div");
      row.dataset.viewKey = `inbox-review-operation:${operation.operation_id}`;
      row.open = record.open;
      row.addEventListener("toggle", () => {
        record.open = row.open;
      });
      record.paint = () => {
        contents.replaceChildren();
        if (record.busy)
          contents.append(el("p", copy(record.busy), "task-impact"));
        appendReviewFailure(contents, record.error, record);
        appendHistoryIdentity(contents, operation);
        contents.append(
          el("p", copy("operation", { p0: operation.operation_id })),
          el(
            "p",
            copy("state", {
              p0: copy(
                ["prepared", "applying", "reconcile", "completed"].includes(
                  operation.state,
                )
                  ? operation.state
                  : "unknown",
              ),
            }),
          ),
        );
        if (operation.can_restore && !record.plan)
          contents.append(
            reviewButton(
              copy("preview_restore"),
              () =>
                run(record, "previewing", async () => {
                  newPlan(
                    record,
                    await context.api.inboxReview.restorePreview(
                      operation.operation_id,
                      context.fixed,
                    ),
                  );
                }),
              !!record.busy,
            ),
          );
        if ((operation.can_reconcile || operation.can_apply) && !record.plan)
          contents.append(
            reviewButton(
              copy("review_pending"),
              () => {
                newPlan(record, operation);
                record.paint();
              },
              !!record.busy,
            ),
          );
        appendOperation(contents, record, context, shared);
      };
      record.paint();
      row.append(contents);
      body.append(row);
    }
  };
  details.addEventListener("toggle", () => {
    state.open = details.open;
    if (state.open && !state.loaded && !state.busy) load();
  });
  state.paint();
  host.append(details);
}
