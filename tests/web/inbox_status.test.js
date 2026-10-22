import test from "node:test";
import assert from "node:assert/strict";
import { isInboxComplete } from "../../src/fiber_bundle/web/inbox_status.js";
import { pendingItems } from "../../src/fiber_bundle/web/home_status.js";

test("only confirmed complete publications leave the pending inbox", () => {
  const complete = {
    id: "saved",
    state: "frozen",
    publication_state: "published",
    intake: { links: [{ published: true }], jobs: [{ state: "complete" }] },
  };
  assert.equal(isInboxComplete(complete), true);
  assert.equal(
    isInboxComplete({ state: "stored", publication_state: "not_published" }),
    false,
  );
  assert.equal(
    isInboxComplete({ state: "complete", publication_state: "not_published" }),
    false,
  );
  assert.equal(
    isInboxComplete({ ...complete, publication_state: "partial" }),
    false,
  );
  for (const state of [
    "needs_review",
    "reconcile",
    "failed",
    "claimed",
    "unknown",
  ])
    assert.equal(
      isInboxComplete({
        ...complete,
        intake: { ...complete.intake, jobs: [{ state }] },
      }),
      false,
    );
  assert.equal(
    isInboxComplete({
      ...complete,
      intake: { ...complete.intake, links: [{ published: false }] },
    }),
    false,
  );
  assert.equal(
    isInboxComplete({
      ...complete,
      intake: { ...complete.intake, source_failures: [{ reason: "missing" }] },
    }),
    false,
  );
  assert.equal(
    isInboxComplete({ ...complete, automatic_state: "needs_review" }),
    false,
  );
  assert.equal(isInboxComplete({ publication: { state: "published" } }), true);
  const input = {
    batches: {
      value: [
        complete,
        { id: "todo", state: "frozen", publication_state: "not_published" },
        {
          ...complete,
          id: "index-failed",
          intake: { jobs: [{ state: "needs_review" }] },
        },
      ],
    },
  };
  assert.deepEqual(
    pendingItems(input).map((x) => x.id),
    ["todo", "index-failed"],
  );
  assert.equal(input.batches.value.length, 3); // history remains available
});
