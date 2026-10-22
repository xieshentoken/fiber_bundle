import { h } from "./dom.js";
export function featureControls(...children) {
  const node = h("div.a-feature", ...children);
  function upgrade(root) {
    if (!(root instanceof window.Element)) return;
    for (const el of [
      root,
      ...root.querySelectorAll(
        "button,input,textarea,select,details,[role=tablist],.record-card,.panel-actions,.rows,.view-panel",
      ),
    ]) {
      if (el.matches("button")) {
        el.classList.add(
          el.getAttribute("role") === "tab" ? "a-tab" : "a-button",
        );
        if (el.classList.contains("primary")) el.classList.add("is-primary");
      } else if (el.matches("select")) el.classList.add("a-input");
      else if (
        el.matches("input:not([type=checkbox]):not([type=radio]),textarea")
      )
        el.classList.add("a-input");
      else if (el.matches("details")) el.classList.add("a-disclosure");
      if (el.matches('[role="tablist"]')) el.classList.add("a-tabs");
      if (el.classList.contains("record-card")) el.classList.add("a-op");
      if (el.classList.contains("panel-actions")) el.classList.add("a-toolbar");
      if (el.classList.contains("rows") || el.classList.contains("view-panel"))
        el.classList.add("a-stack");
    }
  }
  upgrade(node);
  const observer = new window.MutationObserver((records) => {
    for (const record of records)
      for (const added of record.addedNodes) upgrade(added);
  });
  observer.observe(node, { childList: true, subtree: true });
  return { node, dispose: () => observer.disconnect() };
}
