// Lazily expanded, read-only directory tree. Listing never reads note content; obsolete
// loads cannot replace another vault's nodes.
import { h, icon } from "./dom.js";
import { strings } from "./i18n.js";
import { errorText, run } from "./feedback.js";
import { button } from "./ui.js";

const t = strings({
  zh: {
    loading: "正在读取子文件夹…",
    none: "当前层没有可访问的子文件夹。",
    truncated:
      "本层目录过多，列表未完整读取。请展开已列出的目录，或填写明确的相对路径。",
    more: "加载更多子文件夹",
    notTarget: "不能作为写入目标",
  },
  en: {
    loading: "Loading subfolders…",
    none: "No accessible subfolders at this level.",
    truncated:
      "Too many folders here; the list is incomplete. Expand a listed folder or type a relative path.",
    more: "Load more subfolders",
    notTarget: "can't be a write target",
  },
});
export function createDirectoryTree(
  host,
  fetchPage,
  { onNodes = () => {}, onPick } = {},
) {
  let generation = 0;
  const known = new Map();
  host.classList.add("a-tree");
  function clear() {
    generation++;
    known.clear();
    host.replaceChildren();
    onNodes([]);
  }
  async function load(parent, target, cursor, epoch) {
    const page = await fetchPage(parent, cursor);
    if (epoch !== generation || !target.isConnected) return;
    for (const node of page.directories) {
      known.set(node.relative_path, node);
      const children = h("div.a-tree-children");
      const summary = h(
        "summary",
        { title: node.relative_path },
        icon("folder"),
        h("span", node.name),
        node.selectable ? null : h("small.a-subtle", " · " + t("notTarget")),
      );
      if (onPick && node.selectable) {
        const pick = h("button.a-link.a-tree-pick", { type: "button" }, "↵");
        pick.addEventListener("click", (event) => {
          event.preventDefault();
          onPick(node.relative_path);
        });
        summary.append(pick);
      }
      const details = h(
        "details.a-tree-node",
        { dataset: { path: node.relative_path } },
        summary,
        children,
      );
      let loaded = false;
      details.addEventListener("toggle", async () => {
        if (!details.open || loaded || epoch !== generation) return;
        loaded = true;
        children.replaceChildren(h("p.a-subtle", t("loading")));
        try {
          children.replaceChildren();
          await load(node.relative_path, children, null, epoch);
        } catch (error) {
          loaded = false;
          children.replaceChildren(
            h("p.a-inline-error", errorText(error).title),
          );
        }
      });
      target.append(details);
    }
    if (!page.directories.length && !cursor)
      target.append(h("p.a-subtle", t("none")));
    if (page.truncated) target.append(h("p.a-subtle", t("truncated")));
    if (page.next_cursor) {
      const more = button(t("more"), { variant: "small" });
      more.addEventListener("click", () =>
        run(more, async () => {
          await load(parent, target, page.next_cursor, epoch);
          if (epoch === generation) more.remove();
        }),
      );
      target.append(more);
    }
    onNodes([...known.values()]);
  }
  return {
    clear,
    async refresh() {
      clear();
      const epoch = generation;
      host.setAttribute("aria-busy", "true");
      try {
        await load("", host, null, epoch);
      } finally {
        if (epoch === generation) host.removeAttribute("aria-busy");
      }
    },
  };
}
