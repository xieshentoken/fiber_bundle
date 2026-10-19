import { phrase, setText, uiText } from "./i18n/index.js";
// Candidate evidence and review feedback stay beside the card being reviewed.
import { el } from "./ui.js";
import { control, showJSON } from "./record_ui.js";
import { rememberDraft, captureDraftSave } from "./unsaved_changes.js";
export const reviewStates = {
  suggested: "待审核",
  accepted: "已接受线索",
  rejected: "已拒绝",
  stale: "来源已过期",
};
export function candidatePage(items, filter, id, page = 0) {
  const filtered = items.filter((i) => filter === "all" || i.type === filter);
  const index = id
    ? filtered.findIndex(
        (i) => i.id === id && i.value && i.review.state !== "stale",
      )
    : -1;
  return {
    filtered,
    page:
      index >= 0
        ? Math.floor(index / 20)
        : Math.min(page, Math.max(0, Math.ceil(filtered.length / 20) - 1)),
    missing: !!id && index < 0,
  };
}
export function suggestionCard(
  item,
  { entities, review, configuration, scope, epoch, moc, refresh },
) {
  const value = item.value,
    card = el("article", null, "row record"),
    state = el("p"),
    saved = el("p"),
    feedback = el("p"),
    actions = el("div", null, "panel-actions"),
    technical = el("div");
  card.dataset.candidateId = item.id;
  feedback.setAttribute("role", "status");
  feedback.className = "graph-review-feedback";
  const title = !value
    ? "来源已变化，建议不可用"
    : item.type === "moc"
      ? value.title
      : item.type === "entity"
        ? value.label
        : (entities.get(value.source) || "来源提及") +
          " → " +
          (entities.get(value.target) || "来源提及");
  function display() {
    setText(state, uiText(reviewStates[item.review.state]));
    setText(
      saved,
      item.review.reason
        ? phrase("当前审核理由：{p0}；审核时间：{p1}", {
            p0: item.review.reason,
            p1: new Date(item.review.reviewed_at * 1000).toLocaleString(),
          })
        : uiText("尚无审核理由"),
    );
    technical.replaceChildren();
    showJSON(technical, { id: item.id, review: item.review }, "审核记录与标识");
  }
  card.append(el("h3", title), state, saved, feedback);
  if (value) {
    if (value.method)
      card.append(el("p", phrase("方法：{p0}", { p0: value.method })));
    if (value.score != null)
      card.append(
        el(
          "p",
          phrase("模型相似分数：{p0}；这是找资料的线索，需人工核对。", {
            p0: value.score.toFixed(3),
          }),
        ),
      );
    const evidence = Array.isArray(value.evidence)
      ? value.evidence
      : [value.evidence];
    for (const span of evidence) {
      const quote = el("blockquote", span.quote),
        citation =
          (item.paths[span.note_id] || "当前笔记") +
          " · 第 " +
          span.line +
          "–" +
          span.line_end +
          " 行";
      if (span.quote.length > 100 || evidence.length > 3) {
        const details = el("details");
        details.append(
          el("summary", phrase("{p0}（展开原文）", { p0: citation })),
          quote,
        );
        card.append(details);
      } else card.append(el("p", citation), quote);
    }
    const reason = el("textarea");
    reason.dataset.draft = "tracked";
    reason.maxLength = 400;
    reason.value = item.review.reason || "";
    setText(reason, uiText("审核理由"), "aria-label");
    setText(reason, uiText("核对来源后填写理由"), "placeholder");
    card.append(reason);
    let busy = false;
    for (const [label, next] of [
      ["接受线索", "accepted"],
      ["拒绝", "rejected"],
      ["重新核对", "suggested"],
    ]) {
      actions.append(
        control(label, async () => {
          if (busy) return;
          const fixed = epoch(),
            text = reason.value.trim(),
            ack = captureDraftSave(card);
          if (!text) {
            setText(feedback, uiText("请填写审核理由。"));
            feedback.className = "error";
            return;
          }
          busy = true;
          card.dataset.reviewPending = "true";
          setText(feedback, uiText("正在保存审核…"));
          try {
            const decision = await review(item.id, {
              ...scope,
              configuration,
              revision: item.review.revision,
              state: next,
              reason: text,
            });
            if (fixed !== epoch()) return;
            item.review = decision;
            ack();
            display();
            feedback.className = "graph-review-feedback";
            setText(
              feedback,
              uiText("审核已保存；接受的是线索，仍须核对原文。"),
            );
          } catch (error) {
            if (fixed === epoch()) {
              feedback.className = "error";
              setText(
                feedback,
                phrase("审核未保存：{p0}。输入已保留。", {
                  p0: uiText(error.message),
                }),
              );
            }
          } finally {
            busy = false;
            delete card.dataset.reviewPending;
          }
        }),
      );
    }
    if (item.type === "moc")
      actions.append(control("准备 MOC 写回", () => moc.open(item)));
  }
  actions.append(control("刷新并定位此建议", () => refresh(item.id)));
  card.append(actions, technical);
  display();
  rememberDraft(card);
  return card;
}
