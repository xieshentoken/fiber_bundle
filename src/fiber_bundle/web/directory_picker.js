import { phrase, setText, uiText } from "./i18n/index.js";
// One directory picker per editor. Choosing a path only edits the local form draft.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { createDirectoryBrowser } from "./vault_directories.js";

let sequence = 0;
export function createDirectoryPicker(host, fetchPage) {
  const dialog = el("dialog", null, "directory-picker"),
    heading = el("h3"),
    current = el("p", null, "technical"),
    status = el("p", null, "directory-location-status"),
    tree = el("div", null, "vault-directory-tree");
  heading.id = "directory-picker-" + ++sequence;
  dialog.setAttribute("aria-labelledby", heading.id);
  status.setAttribute("role", "status");
  const dismiss = control("取消选择", () => dialog.close());
  let trigger = null,
    field = null,
    generation = 0;
  const browser = createDirectoryBrowser(tree, fetchPage, () => {}, {
    get selectedPath() {
      return field?.value;
    },
    onSelect(node) {
      if (!field || field.disabled) return;
      field.value = node.relative_path;
      field.dispatchEvent(new window.Event("input", { bubbles: true }));
      dialog.close();
    },
  });
  async function refresh() {
    if (!dialog.open) {
      browser.clear();
      return;
    }
    const epoch = ++generation,
      path = field?.value || "";
    setText(
      status,
      path ? uiText("正在定位当前目录…") : uiText("请选择保存目录。"),
    );
    try {
      const result = await browser.refresh(path);
      if (epoch !== generation || !dialog.open || result.state === "cancelled")
        return;
      setText(
        status,
        result.state === "found"
          ? uiText("已定位当前目录。展开箭头可浏览下级，点击“选择”确认。")
          : result.state === "missing"
            ? uiText(
                "当前路径不存在或不在可访问范围内，原路径保留。可选择其他目录，或保留新路径后预览创建。",
              )
            : result.state === "invalid"
              ? uiText(
                  "当前相对路径格式需要核对。原输入保留，可从目录树重新选择。",
                )
              : uiText("请选择保存目录。"),
      );
      if (
        result.element &&
        (document.activeElement === dismiss ||
          document.activeElement === dialog)
      ) {
        result.element.focus({ preventScroll: true });
        result.element.scrollIntoView({
          block: "nearest",
          behavior: "instant",
        });
      }
    } catch (error) {
      if (epoch === generation && dialog.open)
        setText(
          status,
          phrase("目录定位未完成：{p0} 原路径保留，可点击刷新重试。", {
            p0: uiText(error.message),
          }),
        );
    }
  }
  dialog.append(
    heading,
    current,
    status,
    tree,
    control("刷新目录并定位", refresh),
    dismiss,
  );
  dialog.addEventListener("close", () => {
    generation++;
    browser.clear();
    field = null;
    if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    trigger = null;
  });
  host.append(dialog);
  return {
    async open(button, input, title) {
      trigger = button;
      field = input;
      setText(heading, phrase("选择{p0}", { p0: title }));
      setText(
        current,
        phrase("当前路径：{p0}", { p0: input.value || "尚未选择" }),
      );
      dialog.showModal();
      dismiss.focus();
      void refresh();
    },
    refresh,
    clear() {
      generation++;
      if (dialog.open) dialog.close();
      browser.clear();
      dialog.remove();
    },
  };
}
