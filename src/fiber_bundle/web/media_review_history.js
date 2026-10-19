import { phrase, uiText } from "./i18n/index.js";
// Read-only saved judgments; text is never interpreted as markup or draft acceptance.
import { el } from "./ui.js";

export function reviewHistory(review, publication) {
  const root = el("section", null, "media-review-history");
  root.append(el("h4", uiText("最新已保存审核")));
  if (!review) root.append(el("p", uiText("尚无已保存的人工判断。")));
  else {
    const a = review.assessment;
    root.append(
      el("p", phrase("审核记录：{p0}", { p0: review.review_id })),
      el(
        "p",
        phrase("记录时间：{p0}", {
          p0: new Date(review.recorded_at * 1000).toISOString(),
        }),
      ),
      el(
        "p",
        phrase("来源已核对；音频已核对；画面：{p0}", {
          p0: a.visual_reviewed
            ? uiText("已核对")
            : uiText("音频模式，无画面审核"),
        }),
      ),
      el(
        "pre",
        phrase("覆盖区间（ms）：{p0}", {
          p0: JSON.stringify(a.coverage_ranges),
        }),
      ),
    );
    const labels = { accept: "接受", reject: "不接受", uncertain: "待核对" };
    for (const j of a.judgments)
      root.append(
        el("p", j.unit_id + " · " + labels[j.verdict]),
        el("pre", uiText(j.reason)),
      );
    root.append(
      el(
        "p",
        uiText(
          "发布依据为此已保存记录；新判断需重新填写并保存。仍未独立核验。",
        ),
      ),
    );
  }
  if (publication)
    root.append(
      el(
        "p",
        phrase("发布状态：{p0}；审核已冻结。", {
          p0: uiText(publication.state),
        }),
      ),
      el(
        "pre",
        phrase("发布位置：{p0}", {
          p0: publication.entry_note_path || publication.relative_path,
        }),
      ),
    );
  return root;
}
