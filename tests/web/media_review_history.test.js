import test from "node:test";
import assert from "node:assert/strict";
import { reviewHistory } from "../../src/fiber_bundle/web/media_review_history.js";

test("saved review reasons and publication position stay literal; no editable acceptance", () => {
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (tagName) => ({
      tagName,
      children: [],
      append(...nodes) {
        this.children.push(...nodes);
      },
    }),
  };
  try {
    const review = {
      review_id: "synthetic-id",
      recorded_at: 1,
      assessment: {
        visual_reviewed: false,
        coverage_ranges: [{ start_ms: 0, end_ms: 3000 }],
        judgments: [
          {
            unit_id: "T1",
            verdict: "reject",
            reason: "<img onerror=alert(1)>",
          },
        ],
      },
    };
    const root = reviewHistory(review, {
      state: "conflict",
      relative_path: "<script>",
    });
    const text = root.children.map((c) => c.textContent).join("\n");
    assert.ok(
      text.includes("T1 · 不接受") &&
        text.includes("<img onerror=alert(1)>") &&
        text.includes("<script>"),
    );
    assert.ok(
      text.includes("1970-01-01T00:00:01.000Z") && text.includes("审核已冻结"),
    );
    assert.ok(
      root.children.every((c) => ["h4", "p", "pre"].includes(c.tagName)),
    );
    assert.ok(
      reviewHistory(null, null).children.some((c) =>
        c.textContent.includes("尚无"),
      ),
    );
  } finally {
    globalThis.document = previous;
  }
});
