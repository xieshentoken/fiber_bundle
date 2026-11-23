import { rememberDraft, captureDraftSave } from "./unsaved_changes.js";
// Preview form owns its draft and preflight feedback; execution uses a separate confirmation.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { coverageNotice } from "./organization_coverage.js";
import { disclosure } from "./view_sections.js";
import { msg, setText, uiText } from "./i18n/index.js";
import { modeNames } from "./organization_progress.js";
import { unavailableChoice } from "./organization_partial_scope.js";
import { previewSubmitted } from "./organization_workspace.js";
import { appendCatalogFailure } from "./organization_catalog_feedback.js";

export function createMaintenancePreviewForm({
  fixed,
  api,
  refresh,
  draft,
  descriptions,
  catalogReady = true,
  catalogError = null,
}) {
  const formError = el("div");
  const form = el("div"),
    mode = el("select"),
    label = el("label", uiText("本次整理模式")),
    detail = el("p", null, "section-intro");
  setText(mode, uiText("整理模式"), "aria-label");
  for (const [key, title] of Object.entries(modeNames)) {
    const option = el("option", uiText(title));
    option.value = key;
    mode.append(option);
  }
  mode.value = draft.mode;
  setText(detail, uiText(descriptions[mode.value]));
  mode.addEventListener("change", () => {
    draft.mode = mode.value;
    setText(detail, uiText(descriptions[mode.value]));
    detail.className = mode.value === "deep" ? "task-impact" : "section-intro";
  });
  label.append(mode);
  const title = el("input"),
    topic = el("textarea"),
    nameLabel = el("label", uiText("新 MOC 名称")),
    topicLabel = el("label", uiText("主题范围与不包含的内容"));
  title.dataset.draft = topic.dataset.draft = "tracked";
  draft.previewTextSaved ||= {
    title: draft.title || "",
    topic: draft.topic || "",
  };
  const textChanged = () => {
    draft.previewTextEdited =
      title.value !== draft.previewTextSaved.title ||
      topic.value !== draft.previewTextSaved.topic;
  };
  title.value = draft.title;
  topic.value = draft.topic;
  setText(title, uiText("新 MOC 名称"), "aria-label");
  setText(topic, uiText("新 MOC 主题范围"), "aria-label");
  title.addEventListener("input", () => {
    draft.title = title.value;
    textChanged();
  });
  topic.addEventListener("input", () => {
    draft.topic = topic.value;
    textChanged();
  });
  nameLabel.append(title);
  topicLabel.append(topic);
  form.append(
    label,
    detail,
    unavailableChoice(draft),
    disclosure(
      "新建 MOC 与处理说明",
      nameLabel,
      topicLabel,
      el(
        "p",
        uiText(
          "快速模式默认本地分析；全量/深度及显式模型补充沿用统一模型配置。预览不写资料。暂停保留进度；取消撤销本次变更，保留用户修改与调用历史。保护目录与模板不参与。",
        ),
        "muted",
      ),
    ),
    control("生成整理预览", async () => {
      if (draft.previewSubmitting) return;
      draft.previewSubmitting = true;
      const submitted = { title: title.value, topic: topic.value },
        acknowledge = captureDraftSave(form);
      try {
        formError.replaceChildren();
        try {
          await api.maintain({
            ...fixed,
            mode: mode.value,
            unavailable_action: draft.unavailableAction || "moc_only",
            new_mocs: title.value.trim()
              ? [{ title: title.value.trim(), topic: topic.value.trim() }]
              : [],
          });
        } catch (error) {
          if (
            error.code === "maintenance_submission_result_unavailable" ||
            error.code === "maintenance_submission_status_unconfirmed"
          ) {
            formError.append(el("p", msg("api." + error.code), "task-impact"));
            if (error.taskId) formError.append(el("code", error.taskId));
            formError.append(
              control("刷新", async () => {
                previewSubmitted(draft);
                await refresh({ automatic: true, force: true });
              }),
            );
          } else if (
            error.code === "maintenance_submission_not_started" ||
            error.code === "maintenance_submission_changed"
          ) {
            formError.append(el("p", msg("api." + error.code), "task-impact"));
          } else if (appendCatalogFailure(formError, error)) {
            // The scoped preflight did not create a task; preserve the form.
          } else if (error.issues?.length) {
            const notice = coverageNotice(
              {
                complete: false,
                unavailable_count: error.issues.length,
                unavailable: error.issues,
              },
              "maintenance-failed-inputs",
              true,
            );
            formError.append(notice);
          }
          throw error;
        }
        draft.previewTextSaved = submitted;
        textChanged();
        acknowledge();
        // A preview writes only its task ledger. Do not block its result on a full catalog read.
        previewSubmitted(draft);
        await refresh({ automatic: true, force: true });
      } finally {
        draft.previewSubmitting = false;
      }
    }),
    formError,
  );
  if (!catalogReady && !catalogError)
    form.prepend(el("p", msg("organization.recovery.catalog"), "task-impact"));
  const explanation = form.querySelector("details");
  if (explanation) explanation.dataset.viewKey = "maintenance-new-moc";
  rememberDraft(form);
  return form;
}
