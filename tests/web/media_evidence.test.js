import test from "node:test";
import assert from "node:assert/strict";
import {
  sourceEvidence,
  frameEvidence,
} from "../../src/fiber_bundle/web/media_evidence.js";

function dom(work) {
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
    work();
  } finally {
    globalThis.document = previous;
  }
}
const candidate = {
  source_sha256: "a".repeat(64),
  result_sha256: "b".repeat(64),
  glossary_revision: 2,
  glossary_sha256: "c".repeat(64),
};
const context = {
  source_bytes: 200,
  media_type: "video/mp4",
  duration_ms: 44676,
  platform: {
    url: "https://www.bilibili.com/video/BV1XC4y197GR",
    declared_duration_ms: 44767,
    converted_duration_ms: 44676,
    access_state: "public_acquired",
  },
};

test("source evidence distinguishes platform and measured durations with literal exact hashes", () =>
  dom(() => {
    const root = sourceEvidence(context, candidate);
    const text = root.children.map((n) => n.textContent).join("\n");
    assert.ok(text.includes("平台声明时长：44767 ms"));
    assert.ok(text.includes("转换文件测量时长：44676 ms"));
    assert.ok(text.includes(candidate.result_sha256));
    assert.ok(text.includes("不代表完整平台覆盖"));
    const link = root.children.find((n) => n.tagName === "a");
    assert.equal(link.href, context.platform.url);
    assert.equal(link.rel, "noopener noreferrer");
  }));
test("legacy captures never invent converted duration; direct uploads have no platform link", () =>
  dom(() => {
    const legacy = sourceEvidence(
      {
        ...context,
        platform: { ...context.platform, converted_duration_ms: null },
      },
      candidate,
    );
    assert.ok(
      legacy.children.some((n) => n.textContent.includes("未单独测量")),
    );
    const direct = sourceEvidence({ ...context, platform: null }, candidate);
    assert.ok(!direct.children.some((n) => n.tagName === "a"));
    assert.ok(direct.children.some((n) => n.textContent.includes("直接上传")));
  }));
test("unsafe or unrelated platform URLs are never made clickable and injected text stays literal", () =>
  dom(() => {
    for (const url of [
      "javascript:alert(1)",
      "https://example.com/",
      "https://www.bilibili.com/video/BV1XC4y197GR?token=secret",
    ]) {
      const root = sourceEvidence(
        { ...context, platform: { ...context.platform, url } },
        { ...candidate, source_sha256: "<img onerror=alert(1)>" },
      );
      assert.ok(!root.children.some((n) => n.tagName === "a"));
      assert.ok(
        root.children.some((n) =>
          n.textContent.includes("<img onerror=alert(1)>"),
        ),
      );
    }
  }));
test("frame evidence binds the image endpoint to the unit SHA and shows its timestamp", () =>
  dom(() => {
    const frame = { sha256: "d".repeat(64), timestamp_ms: 20000 };
    const root = frameEvidence("a/b", { locator: frame }, [
      { ...frame, timestamp_ms: 10000 },
      frame,
    ]);
    assert.equal(
      root.children[0].src,
      "/admin/media/a%2Fb/frames/" + "d".repeat(64),
    );
    assert.equal(root.children[0].loading, "lazy");
    assert.ok(root.children[1].textContent.includes("20000 ms"));
    assert.ok(root.children[1].textContent.includes("d".repeat(64)));
    assert.equal(
      frameEvidence("a/b", { locator: { ...frame, timestamp_ms: 1 } }, [frame]),
      null,
    );
  }));
