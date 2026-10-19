import { translate } from "./i18n/index.js";
// Session-memory-only draft comparison. No browser storage or credential fields.
let originals = new WeakMap(),
  edited = new WeakSet();
function fields(root) {
  const nodes = Array.from(
    root?.querySelectorAll?.("input,select,textarea") || [],
  );
  if (root?.matches?.("input,select,textarea")) nodes.unshift(root);
  return nodes.filter(
    (node) =>
      node.dataset?.draft !== "ignore" &&
      !node.closest?.('[data-draft="ignore"]') &&
      (node.dataset?.draft === "tracked" ||
        node.closest?.('[data-draft="tracked"]')) &&
      !["password", "file", "hidden"].includes(node.type) &&
      !node.readOnly,
  );
}
function value(node) {
  return ["checkbox", "radio"].includes(node.type) ? node.checked : node.value;
}
export function rememberDraft(root) {
  for (const node of fields(root))
    if (!edited.has(node)) originals.set(node, value(node));
}
export function editDraft(node) {
  if (!fields(node).includes(node)) return;
  if (originals.get(node) === value(node)) edited.delete(node);
  else edited.add(node);
}
export function hasUnsavedChanges(root) {
  return fields(root).some(
    (node) =>
      edited.has(node) &&
      originals.has(node) &&
      originals.get(node) !== value(node),
  );
}
// A successful request acknowledges only its submitted values, never newer edits.
export function captureDraftSave(root) {
  const submitted = fields(root).map((node) => [node, value(node)]);
  return () => {
    for (const [node, saved] of submitted) {
      originals.set(node, saved);
      if (value(node) === saved) edited.delete(node);
      else edited.add(node);
    }
  };
}
export function clearDrafts() {
  originals = new WeakMap();
  edited = new WeakSet();
}

// Native leave confirmation keeps the current DOM intact when the user stays.
export function createDraftGuard(workspace, { extraChanges, signedIn }) {
  let focused = null;
  rememberDraft(workspace);
  workspace.addEventListener("focusin", (event) => {
    rememberDraft(event.target);
    if (event.target.matches("input,select,textarea")) focused = event.target;
  });
  for (const event of ["input", "change"])
    workspace.addEventListener(event, (event) => editDraft(event.target));
  function dirty() {
    const panel = workspace.querySelector(":scope > section:not([hidden])");
    return hasUnsavedChanges(panel) || extraChanges(panel);
  }
  window.addEventListener("beforeunload", (event) => {
    if (signedIn() && dirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  return {
    confirmLeave() {
      const allowed =
        !dirty() ||
        window.confirm(
          translate("本页有未保存的内容，离开可能会清除这些更改。确定离开？"),
        );
      if (!allowed && focused?.isConnected)
        focused.focus({ preventScroll: true });
      return allowed;
    },
    clear() {
      focused = null;
      clearDrafts();
      for (const form of workspace.querySelectorAll("form")) form.reset();
    },
  };
}
