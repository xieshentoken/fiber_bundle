import { phrase, setText, uiText } from "./i18n/index.js";
// Lazily expanded metadata tree; obsolete loads cannot replace another Vault's nodes.
import { el } from "./ui.js";
import { control } from "./record_ui.js";

export function createDirectoryBrowser(
  host,
  fetchPage,
  onNodes = () => {},
  options = {},
) {
  let generation = 0;
  const known = new Map(),
    entries = new Map(),
    pages = new Map();
  function clear() {
    generation++;
    known.clear();
    entries.clear();
    pages.clear();
    host.replaceChildren();
    onNodes([]);
  }
  async function load(parent, target, cursor, epoch) {
    const page = await fetchPage(parent, cursor);
    if (epoch !== generation || !target.isConnected) return;
    if (!cursor) target.replaceChildren();
    for (const node of page.directories) {
      known.set(node.relative_path, node);
      const details = el("details", null, "vault-directory-node"),
        summary = el("summary", node.name),
        children = el("div");
      summary.title = node.relative_path;
      details.dataset.path = node.relative_path;
      const selected = node.relative_path === options.selectedPath;
      if (selected) {
        details.dataset.current = "true";
        summary.append(el("span", uiText(" · 当前目录"), "directory-current"));
      }
      if (!node.selectable)
        summary.append(el("span", uiText(" · 不能作为写入目标"), "muted"));
      let choose = null;
      if (options.onSelect) {
        choose = control("选择", () => options.onSelect(node));
        choose.disabled = !node.selectable;
        choose.dataset.directoryChoice = node.relative_path;
        setText(
          choose,
          phrase("选择目录 {p0}", { p0: node.relative_path }),
          "aria-label",
        );
        choose.setAttribute("aria-pressed", String(selected));
        choose.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
        });
        summary.append(choose);
      }
      let loaded = false,
        pending = null;
      function expand() {
        details.open = true;
        if (pending) return pending;
        if (loaded || epoch !== generation) return Promise.resolve();
        children.replaceChildren(el("p", uiText("正在读取子文件夹…"), "muted"));
        pending = load(node.relative_path, children, null, epoch)
          .then(() => {
            if (epoch === generation) loaded = true;
          })
          .catch((error) => {
            if (epoch === generation && children.isConnected)
              children.replaceChildren(
                el("p", uiText(error.message), "error"),
                control("重试读取子文件夹", expand),
              );
            throw error;
          })
          .finally(() => {
            pending = null;
          });
        return pending;
      }
      details.addEventListener("toggle", () => {
        if (details.open) void expand().catch(() => {});
      });
      details.append(summary, children);
      target.append(details);
      entries.set(node.relative_path, { details, summary, choose, expand });
    }
    if (!page.directories.length && !cursor && !page.next_cursor)
      target.append(el("p", uiText("当前层没有可访问的子文件夹。"), "muted"));
    if (page.truncated)
      target.append(
        el("p", uiText("本层尚有目录未读取，请继续加载。"), "muted"),
      );
    pages.delete(parent);
    if (page.next_cursor) {
      let pending = null;
      const next = () => {
        if (epoch !== generation || !more.isConnected) return Promise.resolve();
        pending ||= load(parent, target, page.next_cursor, epoch)
          .then(() => {
            if (epoch === generation) more.remove();
          })
          .finally(() => {
            pending = null;
          });
        return pending;
      };
      const more = control("加载更多子文件夹", next);
      pages.set(parent, { next, cursor: page.next_cursor });
      target.append(more);
    }
    onNodes([...known.values()]);
  }
  async function reveal(path, epoch) {
    if (!path) return { state: "root" };
    const parts = typeof path === "string" ? path.split("/") : [];
    if (
      !parts.length ||
      parts.some((part) => !part || part === "." || part === "..")
    )
      return { state: "invalid" };
    let parent = "",
      entry = null;
    for (const part of parts) {
      const relative = parent ? parent + "/" + part : part;
      const cursors = new Set();
      while (!entries.has(relative) && pages.has(parent)) {
        const page = pages.get(parent);
        if (cursors.has(page.cursor))
          throw new Error("目录分页未能前进，请刷新后重试。");
        cursors.add(page.cursor);
        await page.next();
        if (epoch !== generation) return { state: "cancelled" };
      }
      entry = entries.get(relative);
      if (!entry) return { state: "missing", path: parent };
      if (relative !== path) await entry.expand();
      if (epoch !== generation) return { state: "cancelled" };
      parent = relative;
    }
    return {
      state: "found",
      path,
      element: entry.choose?.disabled
        ? entry.summary
        : entry.choose || entry.summary,
    };
  }
  return {
    clear,
    async refresh(path = "") {
      clear();
      const epoch = generation;
      host.setAttribute("aria-busy", "true");
      try {
        await load("", host, null, epoch);
        if (epoch !== generation) return { state: "cancelled" };
        return await reveal(path, epoch);
      } finally {
        if (epoch === generation) host.removeAttribute("aria-busy");
      }
    },
  };
}
