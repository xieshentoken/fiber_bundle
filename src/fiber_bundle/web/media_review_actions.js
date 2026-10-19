import { setText, uiText } from "./i18n/index.js";
import { confirmPublication } from "./publication_preview.js";
// Draft edits and publication transitions share one explicit, fail-closed UI state.
import { captureDraftSave, rememberDraft } from "./unsaved_changes.js";
import { captureMessage, setBusy } from "./ui.js";

export function bindReviewActions({
  api,
  job,
  packet,
  form,
  controls,
  save,
  publish,
  gate,
  readAssessment,
  isCurrent,
  onPacket,
}) {
  form.dataset.draft = "tracked";
  rememberDraft(form);
  let savedAssessment = JSON.stringify(readAssessment());
  let dirty = false,
    busy = false,
    publicationUnknown = false;
  const fixedDisabled = controls.map((control) => control.disabled);
  function update() {
    const frozen = !!packet.publication;
    controls.forEach((control, i) => {
      control.disabled =
        fixedDisabled[i] || busy || frozen || publicationUnknown;
    });
    save.disabled = busy || frozen || publicationUnknown;
    publish.disabled =
      busy ||
      dirty ||
      publicationUnknown ||
      !packet.review?.review_id ||
      !packet.review_gate.publication_ready ||
      ["published", "conflict"].includes(packet.publication?.state);
    setText(
      publish,
      packet.publication?.state === "published"
        ? uiText("媒体已发布")
        : packet.publication?.state === "conflict"
          ? uiText("发布冲突，需核对")
          : frozen
            ? uiText("核对并恢复媒体发布")
            : uiText("发布已保存审核的媒体"),
    );
    setText(
      gate,
      publicationUnknown
        ? uiText("发布状态暂不可确认，请刷新后核对；审核已暂时锁定。")
        : frozen
          ? uiText("任务已进入发布流程，审核冻结。")
          : dirty
            ? uiText("有未保存的新判断，保存或刷新前不能发布。")
            : packet.review_gate.publication_ready
              ? uiText("人工审核已保存，可显式发布；仍未独立核验。")
              : uiText("需核对完整来源、音频、画面及转录疑点。"),
    );
  }
  for (const control of controls)
    for (const event of ["input", "change"])
      control.addEventListener(event, () => {
        dirty = JSON.stringify(readAssessment()) !== savedAssessment;
        update();
      });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || packet.publication || publicationUnknown || !isCurrent())
      return;
    const notify = captureMessage("media");
    const acknowledge = captureDraftSave(form);
    const submitted = readAssessment();
    const submittedAssessment = JSON.stringify(submitted);
    dirty = true;
    busy = true;
    setBusy(save, true);
    update();
    try {
      const result = await api.reviewMedia(job, submitted);
      const fresh = await api.mediaPacket(job);
      if (!isCurrent()) return;
      packet = fresh;
      onPacket(fresh);
      if (fresh.review?.review_id !== result.review_id)
        throw new Error("已保存审核记录发生变化，请刷新并核对最新记录。");
      savedAssessment = submittedAssessment;
      dirty = JSON.stringify(readAssessment()) !== savedAssessment;
      acknowledge();
      notify("媒体判断已记录，已重新读取保存的判断与理由。");
    } catch (error) {
      if (isCurrent()) notify(error.message, true);
    } finally {
      busy = false;
      setBusy(save, false);
      if (isCurrent()) update();
    }
  });
  publish.addEventListener("click", async () => {
    if (publish.disabled || busy || !isCurrent()) return;
    const notify = captureMessage("media");
    const displayedReview = packet.review.review_id;
    busy = true;
    setBusy(publish, true);
    update();
    let failure;
    try {
      if (
        !(await confirmPublication(api, "media", job, {
          review_id: displayedReview,
        }))
      ) {
        busy = false;
        setBusy(publish, false);
        if (isCurrent()) update();
        return;
      }
      if (!isCurrent()) return;
      const result = await api.publishMedia(job, {
        review_id: displayedReview,
      });
      if (isCurrent()) notify("媒体发布状态：" + result.state);
    } catch (error) {
      failure = error;
    }
    // Even a failed response may have prepared or renamed a publication.
    try {
      const fresh = await api.mediaPacket(job);
      if (!isCurrent()) return;
      packet = fresh;
      onPacket(fresh);
      if (
        fresh.review?.review_id !== displayedReview &&
        (!failure || failure.status === 422)
      )
        failure = new Error("审核记录已变化，请核对最新保存的判断后再发布。");
    } catch (error) {
      publicationUnknown = true;
      failure = error;
    } finally {
      busy = false;
      setBusy(publish, false);
      if (isCurrent()) {
        update();
        if (failure) notify(failure.message, true);
      }
    }
  });
  update();
}
