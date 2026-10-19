import { setText, textNode, uiText } from "./i18n/index.js";
// Selection is scope/version-bound operation state, never an editable draft.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
export const GRAPH_NOTE_LIMIT = 100;
export function createNoteSelection() {
  let scope = "",
    available = new Map(),
    selected = new Map();
  return {
    update(nextScope, notes) {
      if (nextScope !== scope) selected.clear();
      scope = nextScope;
      available = new Map(
        notes
          .filter((n) => n.selectable && n.note_id && n.sha256)
          .map((n) => [n.note_id, n]),
      );
      selected = new Map(
        [...selected].filter(([id, sha]) => available.get(id)?.sha256 === sha),
      );
    },
    choose(id, checked) {
      const note = available.get(id);
      if (!note) return;
      if (checked) selected.set(id, note.sha256);
      else selected.delete(id);
    },
    bulk(operation) {
      for (const [id, note] of available) {
        if (
          operation === "none" ||
          (operation === "invert" && selected.has(id))
        )
          selected.delete(id);
        else selected.set(id, note.sha256);
      }
    },
    has: (id) => selected.has(id),
    counts: () => ({
      selected: selected.size,
      selectable: available.size,
      limit: GRAPH_NOTE_LIMIT,
    }),
    payload() {
      if (selected.size > GRAPH_NOTE_LIMIT)
        throw new Error("单任务最多 100 篇，请减少选择后提交。");
      return [...selected].map(([note_id, expected_sha256]) => ({
        note_id,
        expected_sha256,
      }));
    },
    clear() {
      scope = "";
      available.clear();
      selected.clear();
    },
  };
}
export function renderNoteSelection(
  root,
  selection,
  notes,
  scope,
  enabled,
  submit,
) {
  selection.update(scope, enabled ? notes : []);
  root.replaceChildren();
  const toolbar = el("div", null, "row"),
    count = el("p");
  toolbar.dataset.draft = "ignore";
  const checks = [];
  function update() {
    const c = selection.counts(),
      invalid = !enabled || !c.selected || c.selected > c.limit;
    setText(
      count,
      uiText(
        `已选 ${c.selected} / 可选 ${c.selectable}；单任务最多 ${c.limit} 篇` +
          (c.selected > c.limit ? "，已超限，请减少选择。" : ""),
      ),
    );
    count.setAttribute("role", "status");
    submit.disabled = invalid;
    submit.dataset.policyDisabled = String(invalid);
    for (const check of checks)
      check.checked = selection.has(check.note.note_id);
  }
  for (const [label, op] of [
    ["全选", "all"],
    ["取消全选", "none"],
    ["反选", "invert"],
  ]) {
    const button = control(label, () => {
      selection.bulk(op);
      update();
    });
    button.disabled = !enabled;
    button.dataset.policyDisabled = String(!enabled);
    toolbar.append(button);
  }
  root.append(toolbar, count);
  for (const note of notes) {
    const label = el("label"),
      check = el("input");
    check.type = "checkbox";
    check.disabled =
      !enabled || !note.selectable || !note.note_id || !note.sha256;
    check.dataset.draft = "ignore";
    check.note = note;
    checks.push(check);
    check.addEventListener("change", () => {
      selection.choose(note.note_id, check.checked);
      update();
    });
    label.append(
      check,
      textNode(
        uiText(
          note.title +
            " · " +
            note.note_path +
            (note.selectable ? "" : "（尚未登记稳定身份）"),
        ),
      ),
    );
    root.append(label);
  }
  update();
}
