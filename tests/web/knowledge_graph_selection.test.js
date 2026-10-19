import test from "node:test";
import assert from "node:assert/strict";
import { createNoteSelection } from "../../src/fiber_bundle/web/knowledge_graph_selection.js";
import { candidatePage } from "../../src/fiber_bundle/web/knowledge_graph_cards.js";
const note = (id, sha = "a", selectable = true) => ({
  note_id: id,
  sha256: sha,
  selectable,
});
test("bulk selection excludes disabled entries and binds refreshed source versions", () => {
  const s = createNoteSelection();
  s.update("client:vault", [note("a"), note("b"), note("c", "a", false)]);
  s.bulk("all");
  assert.equal(s.counts().selected, 2);
  s.bulk("invert");
  assert.equal(s.counts().selected, 0);
  s.choose("c", true);
  assert.equal(s.counts().selected, 0);
  s.choose("a", true);
  s.update("client:vault", [note("a", "changed"), note("b")]);
  assert.equal(s.counts().selected, 0);
  s.bulk("all");
  s.update("other:vault", [note("a", "changed")]);
  assert.equal(s.counts().selected, 0);
});
test("over limit rejects rather than silently truncating and cancellation is explicit", () => {
  const s = createNoteSelection();
  s.update(
    "scope",
    Array.from({ length: 101 }, (_, i) => note(String(i))),
  );
  s.bulk("all");
  assert.throws(() => s.payload(), /最多 100/);
  s.choose("100", false);
  assert.equal(s.payload().length, 100);
  s.bulk("none");
  assert.deepEqual(s.payload(), []);
});
test("stable candidate ID restores page and refuses stale, missing, and filtered IDs", () => {
  const items = Array.from({ length: 42 }, (_, i) => ({
    id: String(i),
    type: "entity",
    value: {},
    review: { state: "suggested" },
  }));
  assert.equal(candidatePage(items, "all", "30").page, 1);
  assert.equal(candidatePage(items, "moc", "30").missing, true);
  items[30].review.state = "stale";
  assert.equal(candidatePage(items, "all", "30").missing, true);
  assert.equal(candidatePage(items, "all", "removed").missing, true);
  assert.equal(candidatePage(items.slice(0, 5), "all", null, 3).page, 0);
});
