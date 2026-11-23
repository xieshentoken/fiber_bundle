// Preparing a new task changes presentation only; durable results stay in history.
import { el } from "./ui.js";
import { disclosure } from "./view_sections.js";
import { joinText, phrase, uiText } from "./i18n/index.js";
import { taskRecordSummary } from "./organization_task_record.js";
import { emptyCandidatePreview } from "./organization_candidate_coverage.js";
import {
  jobNames,
  modeNames,
  previewInvalidated,
} from "./organization_progress.js";

export function workspaceState(jobs, draft) {
  const current = latestTask(jobs);
  if (draft.preparingFor && draft.preparingFor !== current?.id)
    delete draft.preparingFor;
  const preparing =
    !!current &&
    draft.preparingFor === current.id &&
    ["completed", "cancelled", "closed", "preview"].includes(current.state);
  return {
    current: preparing ? null : current,
    history: jobs
      .filter((job) => preparing || job !== current)
      .slice()
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0)),
  };
}

function latestTask(jobs) {
  // API task order can prioritize an older interrupted task. Presentation uses
  // creation order so an old paused task cannot replace a newly prepared task.
  return jobs.reduce(
    (latest, job) =>
      !latest || (job.created_at || 0) > (latest.created_at || 0)
        ? job
        : latest,
    undefined,
  );
}

export function previewSubmitted(draft) {
  delete draft.preparingFor;
  // An identical request can safely return the same preview from the backend.
  draft.previewVersion = (draft.previewVersion || 0) + 1;
}

export function mountMaintenanceWorkspace(
  host,
  { jobs, draft, form, renderJob },
) {
  const latest = latestTask(jobs);
  if (!latest) {
    delete draft.preparingFor;
    host.append(form);
    return form;
  }
  workspaceState(jobs, draft);
  const main = el("div"),
    historyHost = el("div");
  const preparation = disclosure("准备另一项整理／重新生成预览", form);
  // View restoration must not reopen the previous task's preparation on a new preview.
  preparation.dataset.viewKey = `maintenance-preparation:${latest.id}:${draft.previewVersion || 0}`;
  preparation.open =
    draft.preparingFor === latest.id || previewInvalidated(latest);
  function paint() {
    if (preparation.open) draft.preparingFor = latest.id;
    else delete draft.preparingFor;
    const state = workspaceState(jobs, draft);
    main.replaceChildren();
    historyHost.replaceChildren();
    if (state.current)
      main.append(el("h3", uiText("当前任务")), renderJob(state.current));
    else
      main.append(
        el("p", uiText("正在准备新整理；此前结果保留在历史任务中。"), "muted"),
      );
    const executing = state.history.filter((job) =>
      ["queued", "running", "cancelling"].includes(job.state),
    );
    if (executing.length)
      main.append(
        el(
          "p",
          phrase("其他任务仍标为执行中：{p0}。请展开对应记录核对。", {
            p0: executing.map((job) => job.id).join("、"),
          }),
          "task-impact",
        ),
      );
    if (!state.history.length) return;
    const history = disclosure(`其他与历史任务（${state.history.length}）`);
    history.dataset.viewKey = "maintenance-history";
    for (const job of state.history) {
      const details = disclosure(
        joinText(
          [
            uiText(modeNames[job.mode]),
            uiText(
              emptyCandidatePreview(job)
                ? "无需确认"
                : previewInvalidated(job)
                  ? "预览已失效"
                  : jobNames[job.state] || job.state,
            ),
            taskRecordSummary(job),
          ],
          " · ",
        ),
      );
      details.dataset.viewKey = `job:${job.id}`;
      details.addEventListener("toggle", () => {
        if (details.open && details.children.length === 1)
          details.append(renderJob(job));
      });
      history.append(details);
    }
    historyHost.append(history);
  }
  preparation.addEventListener("toggle", paint);
  host.append(main, preparation, historyHost);
  paint();
  return preparation;
}
