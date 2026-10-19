import { phrase, setText, uiText } from "./i18n/index.js";
import { createVideoPanel } from "./video_processing.js";
// Explicit human audio/visual assessments; all evidence is rendered as literal text.
import { $, el, checkbox, message, setBusy } from "./ui.js";
import { sourceEvidence, frameEvidence } from "./media_evidence.js";
import { reviewHistory } from "./media_review_history.js";
import { bindReviewActions } from "./media_review_actions.js";
import { mediaState, publicationState, shortId } from "./state_labels.js";

export function assessment(packet, source, audio, visual, decisions) {
  const c = packet.candidate;
  if (!source || !audio || visual !== (packet.mode === "full_evidence"))
    throw new Error("请核对完整来源和音频，并明确画面核对状态。");
  if (
    decisions.length !== packet.units.length ||
    decisions.some((d) => !d.reason.trim())
  )
    throw new Error("请为每个来源单元记录实际判断和理由。");
  return {
    source_sha256: c.source_sha256,
    result_sha256: c.result_sha256,
    glossary_revision: c.glossary_revision,
    glossary_sha256: c.glossary_sha256,
    source_reviewed: true,
    audio_reviewed: true,
    visual_reviewed: visual,
    coverage_ranges: [{ start_ms: 0, end_ms: c.duration_ms }],
    judgments: decisions,
  };
}

export function createMediaView(api) {
  const video = createVideoPanel(api);
  let revision = 0;
  const container = () => $("media-list");
  function clear() {
    video.clear();
    revision++;
    container().replaceChildren();
  }
  function render(task, packet, current) {
    if (current !== revision) return;
    const root = el("article", null, "document-review");
    const gate = el(
      "p",
      packet.review_gate.publication_ready
        ? uiText("人工审核已记录；仍未独立核验。")
        : uiText("需核对完整来源、音频、画面及转录疑点。"),
    );
    const history = el("div");
    history.append(reviewHistory(packet.review, packet.publication));
    const original = el("a", uiText("下载完整原件并核对"));
    original.href =
      "/admin/media/" + encodeURIComponent(task.job_id) + "/source";
    original.download = "";
    root.append(
      el("h3", uiText("媒体来源核对")),
      gate,
      history,
      sourceEvidence(packet.source_context, packet.candidate),
      original,
      el(
        "p",
        packet.mode === "audio_only"
          ? uiText("音频优先整理，未核验画面内容")
          : uiText("关键帧是抽样证据，不能替代完整视频核对。"),
      ),
      el(
        "p",
        phrase("时间定位依据：{p0}", {
          p0: packet.candidate.transcript.timestamp_basis,
        }),
      ),
    );
    if (!packet.candidate.complete)
      root.append(
        el("p", uiText("转录覆盖或术语疑点未解决，不能发布。"), "error"),
      );
    if (packet.candidate.historical) {
      original.remove();
      root.append(
        el("p", uiText("链接媒体已按确认清理；转录、关键帧和审计记录保留。")),
      );
    }
    const form = el("form");
    const source = checkbox("我已核对完整来源及全时长覆盖", false);
    const audio = checkbox("我已核对音频、转录、数字和术语", false);
    const visual = checkbox(
      "我已核对完整视频画面，抽样帧以外的内容也已检查",
      false,
    );
    if (packet.mode === "audio_only") visual.input.disabled = true;
    const decisions = [];
    for (const unit of packet.units) {
      const block = el("fieldset");
      const verdict = el("select");
      const reason = el("textarea");
      block.append(
        el("legend", unit.unit_id),
        el("pre", JSON.stringify(unit.locator, null, 2)),
        el("pre", unit.text),
      );
      const frame = frameEvidence(task.job_id, unit, packet.candidate.frames);
      if (frame) block.append(frame);
      for (const [value, label] of [
        ["uncertain", "待核对"],
        ["reject", "不接受"],
        ["accept", "已核对，接受"],
      ]) {
        const option = el("option", uiText(label));
        option.value = value;
        verdict.append(option);
      }
      setText(verdict, phrase("{p0} 判断", { p0: unit.unit_id }), "aria-label");
      setText(reason, phrase("{p0} 理由", { p0: unit.unit_id }), "aria-label");
      reason.required = true;
      reason.maxLength = 2000;
      block.append(verdict, reason);
      form.append(block);
      decisions.push({ unit, verdict, reason });
    }
    for (const correction of packet.candidate.transcript.corrections) {
      form.append(
        el(
          "pre",
          phrase("原始：{p0}\n规范化：{p1}", {
            p0: correction.original,
            p1: correction.normalized,
          }),
        ),
      );
    }
    for (const issue of packet.candidate.transcript.ambiguities)
      form.append(el("pre", JSON.stringify(issue)));
    const save = el("button", uiText("记录媒体人工审核"));
    save.type = "submit";
    const publish = el("button", uiText("发布已审核媒体"));
    publish.type = "button";
    publish.disabled = !packet.review_gate.publication_ready;
    const plan = el("button", uiText("查看链接媒体清理范围"));
    plan.type = "button";
    form.append(source.label, audio.label, visual.label, save, publish, plan);
    bindReviewActions({
      api,
      job: task.job_id,
      packet,
      form,
      save,
      publish,
      gate,
      controls: [
        source.input,
        audio.input,
        visual.input,
        ...decisions.flatMap(({ verdict, reason }) => [verdict, reason]),
      ],
      isCurrent: () => current === revision,
      onPacket: (fresh) =>
        history.replaceChildren(reviewHistory(fresh.review, fresh.publication)),
      readAssessment: () =>
        assessment(
          packet,
          source.input.checked,
          audio.input.checked,
          visual.input.checked,
          decisions.map(({ unit, verdict, reason }) => ({
            unit_id: unit.unit_id,
            verdict: verdict.value,
            reason: reason.value,
          })),
        ),
    });
    plan.addEventListener("click", async () => {
      plan.disabled = true;
      setBusy(plan, true);
      try {
        const value = await api.mediaCleanupPlan(task.job_id);
        if (current !== revision) return;
        const confirm = checkbox(
          "我确认清理此任务自有的 " +
            value.file_count +
            " 个媒体副本（" +
            value.bytes +
            " 字节），保留转录、关键帧和审计。",
          false,
        );
        const apply = el("button", uiText("按此清单清理"));
        apply.type = "button";
        apply.disabled = true;
        confirm.input.addEventListener("change", () => {
          apply.disabled = !confirm.input.checked;
        });
        form.append(confirm.label, apply);
        apply.addEventListener("click", async () => {
          apply.disabled = true;
          setBusy(apply, true);
          try {
            const result = await api.mediaCleanup(task.job_id, {
              confirmation_sha256: value.confirmation_sha256,
            });
            if (current === revision) {
              original.remove();
              message("链接媒体清理状态：" + result.state, false, "media");
            }
          } catch (error) {
            if (current === revision) message(error.message, true, "media");
          } finally {
            setBusy(apply, false);
          }
        });
      } catch (error) {
        if (current === revision) message(error.message, true, "media");
      } finally {
        setBusy(plan, false);
        if (current === revision) plan.disabled = false;
      }
    });
    root.append(form);
    container().append(root);
  }
  async function refresh() {
    const current = ++revision;
    await video.refresh();
    container().replaceChildren(el("p", uiText("正在读取媒体任务…")));
    const list = await api.media();
    if (current !== revision) return;
    container().replaceChildren();
    if (!list.tasks.length) {
      container().append(el("p", uiText("暂无媒体处理任务。")));
      return;
    }
    for (const task of list.tasks) {
      const row = el("article", null, "ui-task");
      const button = el("button", uiText("查看媒体证据"));
      button.disabled = task.state !== "complete";
      row.dataset.taskKind = "media";
      row.dataset.taskId = task.job_id;
      const main = el("div", null, "ui-task-main"),
        id = el(
          "span",
          phrase("任务 {p0}", { p0: shortId(task.job_id) }),
          "technical",
        ),
        meta = el("small"),
        states = el("div", null, "ui-task-states"),
        actions = el("div", null, "ui-task-actions");
      id.title = task.job_id;
      meta.append(id);
      main.append(el("strong", uiText("媒体任务")), meta);
      const [state, tone] = mediaState(task.state);
      states.append(el("span", uiText(state), "status-badge " + tone));
      if (task.publication_state) {
        const [pub, pubTone] = publicationState(task.publication_state);
        states.append(el("span", uiText(pub), "status-badge " + pubTone));
      }
      actions.append(button);
      row.append(main, states, actions);
      container().append(row);
      button.addEventListener("click", async () => {
        button.disabled = true;
        setBusy(button, true);
        try {
          const packet = await api.mediaPacket(task.job_id);
          if (current === revision) render(task, packet, current);
        } catch (error) {
          if (current === revision) message(error.message, true, "media");
        } finally {
          setBusy(button, false);
          if (current === revision) button.disabled = false;
        }
      });
    }
  }
  return {
    clear,
    refresh,
    selectVideoScope: (cid, vid) => video.selectScope(cid, vid),
  };
}
