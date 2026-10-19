import test from "node:test";
import assert from "node:assert/strict";
import { bindReviewActions } from "../../src/fiber_bundle/web/media_review_actions.js";

function control(disabled = false) {
  const handlers = {};
  return {
    disabled,
    dataset: {},
    attributes: new Map(),
    setAttribute(name, value) {
      this.attributes.set(name, value);
    },
    removeAttribute(name) {
      this.attributes.delete(name);
    },
    addEventListener(name, work) {
      handlers[name] = work;
    },
    emit(name) {
      return handlers[name]({ preventDefault() {} });
    },
  };
}
function fixture(
  api,
  publication = null,
  review = { review_id: "displayed-review" },
) {
  const notice = {};
  globalThis.window = { confirm: () => true };
  api.previewPublication = async () => ({
    state: "publication_preview",
    publication_id: "synthetic",
    entry_note_path: "Inbox/Synthetic.md",
    paths: ["Inbox/Synthetic.md"],
  });
  api.discardPublicationPreview = async () => ({});
  globalThis.document = { getElementById: () => notice };
  const form = control(),
    save = control(),
    publish = control(),
    gate = {};
  const controls = [control(), control(true)];
  const packet = {
    review,
    review_gate: { publication_ready: true },
    publication,
  };
  const updates = [];
  bindReviewActions({
    api,
    job: "synthetic",
    packet,
    form,
    controls,
    save,
    publish,
    gate,
    readAssessment: () => ({ synthetic: controls[0].value || "" }),
    isCurrent: () => true,
    onPacket: (fresh) => updates.push(fresh),
  });
  return { form, save, publish, controls, gate, packet, notice, updates };
}

test("draft edits block saved-review publication until exact successful save and reread", async () => {
  let resolve;
  let published = 0;
  const api = {
    reviewMedia: () =>
      new Promise((r) => {
        resolve = r;
      }),
    mediaPacket: async () => ({
      review: { review_id: "new" },
      review_gate: { publication_ready: false },
      publication: null,
    }),
    publishMedia: async () => {
      published++;
    },
  };
  const f = fixture(api);
  assert.equal(f.publish.disabled, false);
  f.controls[0].value = "edited";
  f.controls[0].emit("input");
  assert.equal(f.publish.disabled, true);
  await f.publish.emit("click");
  assert.equal(published, 0);
  const pending = f.form.emit("submit");
  assert.ok(f.controls.every((c) => c.disabled) && f.save.disabled);
  assert.equal(f.save.attributes.get("aria-busy"), "true");
  assert.equal(f.publish.attributes.has("aria-busy"), false);
  resolve({ review_id: "new" });
  await pending;
  assert.equal(f.publish.disabled, true);
  assert.equal(f.controls[0].disabled, false);
  assert.equal(f.controls[1].disabled, true);
  assert.equal(f.save.attributes.has("aria-busy"), false);
  assert.equal(f.updates.length, 1);
});

test("successful accept save permits explicit publication; stale or failed reread never does", async () => {
  for (const outcome of ["exact", "different", "network"]) {
    const f = fixture({
      reviewMedia: async () => ({ review_id: "saved" }),
      mediaPacket: async () => {
        if (outcome === "network") throw new Error("offline");
        return {
          review: { review_id: outcome === "exact" ? "saved" : "other" },
          review_gate: { publication_ready: true },
          publication: null,
        };
      },
    });
    f.controls[0].value = "edited";
    f.controls[0].emit("change");
    await f.form.emit("submit");
    assert.equal(f.publish.disabled, outcome !== "exact");
    assert.equal(f.save.disabled, false);
  }
});

test("all publication rows freeze review; only prepared/publishing offer explicit recovery", async () => {
  for (const state of ["prepared", "publishing", "conflict", "published"]) {
    let saved = 0;
    const f = fixture(
      {
        reviewMedia: async () => {
          saved++;
        },
      },
      { state },
    );
    assert.ok(f.save.disabled && f.controls.every((c) => c.disabled));
    assert.equal(f.publish.disabled, ["conflict", "published"].includes(state));
    await f.form.emit("submit");
    assert.equal(saved, 0);
  }
});

test("publish response success or failure rechecks effects and preserves freeze", async () => {
  for (const response of ["success", "crash", "unknown"]) {
    const f = fixture({
      publishMedia: async () => {
        if (response !== "success")
          throw new Error("synthetic interrupted response");
        return { state: "published" };
      },
      mediaPacket: async () => {
        if (response === "unknown") throw new Error("offline");
        return {
          review: { review_id: "displayed-review" },
          publication: {
            state: response === "crash" ? "publishing" : "published",
          },
          review_gate: { publication_ready: true },
        };
      },
    });
    await f.publish.emit("click");
    assert.ok(f.save.disabled && f.controls.every((c) => c.disabled));
    assert.equal(f.publish.disabled, response !== "crash");
  }
});

test("publication sends the displayed review and refreshes replaced judgments without retry", async () => {
  for (const ready of [true, false]) {
    const calls = [];
    const f = fixture({
      publishMedia: async (job, body) => {
        calls.push({ job, body });
        throw Object.assign(new Error("invalid_input"), { status: 422 });
      },
      mediaPacket: async () => ({
        review: { review_id: "newer-review" },
        publication: null,
        review_gate: { publication_ready: ready },
      }),
    });
    await f.publish.emit("click");
    assert.deepEqual(calls, [
      { job: "synthetic", body: { review_id: "displayed-review" } },
    ]);
    assert.equal(f.updates[0].review.review_id, "newer-review");
    assert.ok(f.notice.textContent.includes("审核记录已变化"));
    assert.equal(f.publish.disabled, !ready);
    if (ready) {
      await f.publish.emit("click");
      assert.equal(calls[1].body.review_id, "newer-review");
    }
  }
});

test("a ready gate without a displayed review can never trigger bodyless publication", async () => {
  let called = false;
  const f = fixture(
    {
      publishMedia: async () => {
        called = true;
      },
    },
    null,
    null,
  );
  await f.publish.emit("click");
  assert.equal(called, false);
  assert.equal(f.publish.disabled, true);
});

test("a changed review never hides a CSRF failure or retries it automatically", async () => {
  const f = fixture({
    publishMedia: async () => {
      throw Object.assign(new Error("refresh session"), { status: 403 });
    },
    mediaPacket: async () => ({
      review: { review_id: "newer-review" },
      publication: null,
      review_gate: { publication_ready: true },
    }),
  });
  await f.publish.emit("click");
  assert.equal(f.notice.textContent, "refresh session");
  assert.equal(f.updates[0].review.review_id, "newer-review");
});

test("restoring the displayed assessment removes the publication draft block", () => {
  const f = fixture({});
  f.controls[0].value = "new";
  f.controls[0].emit("input");
  assert.equal(f.publish.disabled, true);
  f.controls[0].value = "";
  f.controls[0].emit("input");
  assert.equal(f.publish.disabled, false);
});

test("a later edit is retained when the submitted assessment is acknowledged", async () => {
  let resolve;
  const f = fixture({
    reviewMedia: () =>
      new Promise((r) => {
        resolve = r;
      }),
    mediaPacket: async () => ({
      review: { review_id: "saved" },
      review_gate: { publication_ready: true },
      publication: null,
    }),
  });
  f.controls[0].value = "submitted";
  f.controls[0].emit("input");
  const pending = f.form.emit("submit");
  f.controls[0].value = "";
  f.controls[0].emit("input");
  resolve({ review_id: "saved" });
  await pending;
  assert.equal(f.publish.disabled, true);
  f.controls[0].value = "submitted";
  f.controls[0].emit("input");
  assert.equal(f.publish.disabled, false);
});
