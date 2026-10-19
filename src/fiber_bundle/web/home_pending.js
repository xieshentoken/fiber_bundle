import { renderInbox } from "./inbox.js";
// Compatibility for the former grouped worklist renderer.
export function renderPending(root, records, detailed = false, scopeText = "") {
  void detailed;
  return renderInbox(root, records, scopeText, () =>
    renderPending(root, records, detailed, scopeText),
  );
}
