import { phrase, setText, uiText } from "./i18n/index.js";
// One accessible modal shared by the logo and settings; no business API calls.
import { THEMES, themeInfo } from "./themes.js";

export function createThemePicker(
  appearance,
  {
    onSelected = () => {},
    beforeSelected = () => true,
    onRendererSelected = () => {},
  } = {},
) {
  const dialog = document.createElement("dialog");
  dialog.id = "theme-picker";
  dialog.className = "theme-picker";
  dialog.setAttribute("aria-labelledby", "theme-picker-title");
  dialog.setAttribute("aria-describedby", "theme-picker-description");
  const heading = document.createElement("div");
  heading.className = "theme-picker-heading";
  const title = document.createElement("h2");
  title.id = "theme-picker-title";
  setText(title, uiText("选择主题"));
  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.className = "quiet";
  setText(dismiss, uiText("关闭主题选择"), "aria-label");
  setText(dismiss, uiText("关闭"));
  heading.append(title, dismiss);
  const description = document.createElement("p");
  description.id = "theme-picker-description";
  description.className = "muted";
  setText(
    description,
    uiText(
      "点击卡片切换当前外观；设为默认后，下次启动或刷新使用该主题。字体与字号保留。",
    ),
  );
  const cards = document.createElement("div");
  cards.className = "theme-picker-cards";
  const error = document.createElement("p");
  error.className = "theme-picker-error";
  error.setAttribute("role", "alert");
  error.hidden = true;
  const buttons = new Map();
  let position = null;
  let trigger = null,
    destroyed = false;
  function restoreScroll() {
    if (position)
      window.scrollTo({
        left: position.x,
        top: position.y,
        behavior: "instant",
      });
  }
  function close() {
    if (dialog.open) {
      dialog.close();
      trigger?.setAttribute("aria-expanded", "false");
      restoreScroll();
    }
  }
  function select(id) {
    try {
      const changed = appearance.current.theme !== id;
      if (changed && !beforeSelected(themeInfo(id))) return;
      const previous = appearance.current.theme;
      appearance.selectTheme(id);
      error.hidden = true;
      setText(status, uiText("当前外观已切换；默认主题保持不变。"));
      if (changed) {
        onSelected(themeInfo(id));
        if ((previous === "atlas") !== (id === "atlas"))
          onRendererSelected(themeInfo(id));
      }
    } catch {
      setText(error, uiText("无法切换主题，请重试。未保存内容已保留。"));
      error.hidden = false;
    }
  }
  function choice(theme) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "theme-choice";
    button.dataset.themeChoice = theme.id;
    {
      const sample = document.createElement("span");
      sample.className = "theme-sample";
      sample.dataset.theme = theme.id;
      sample.setAttribute("aria-hidden", "true");
      // Same miniature workspace, with local theme tokens rather than remote images.
      const rail = document.createElement("span");
      rail.className = "theme-sample-rail";
      const content = document.createElement("span");
      content.className = "theme-sample-content";
      const label = document.createElement("span");
      label.className = "theme-sample-title";
      setText(label, uiText("Fiber bundle"));
      const action = document.createElement("span");
      action.className = "theme-sample-action";
      setText(action, uiText("导入"));
      const panel = document.createElement("span");
      panel.className = "theme-sample-panel";
      for (const [tag, text] of [
        ["strong", "研究笔记"],
        ["small", "整理资料，随时查阅。"],
        ["span", "✓ 已入库"],
      ]) {
        const item = document.createElement(tag);
        setText(item, uiText(text));
        panel.append(item);
      }
      content.append(label, action, panel);
      sample.append(rail, content);
      button.append(sample);
    }
    const name = document.createElement("span");
    name.className = "theme-choice-name";
    setText(name, uiText(theme.name));
    const selected = document.createElement("span");
    selected.className = "theme-choice-current";
    setText(selected, uiText("✓ 当前"));
    const defaultMarker = document.createElement("span");
    defaultMarker.className = "theme-choice-default";
    setText(defaultMarker, uiText("默认主题"));
    const markers = document.createElement("span");
    markers.className = "theme-choice-markers";
    markers.append(selected, defaultMarker);
    button.append(name, markers);
    if (theme.description) {
      const detail = document.createElement("span");
      detail.className = "theme-choice-description";
      setText(detail, uiText(theme.description));
      button.append(detail);
    }
    button.addEventListener("click", () => select(theme.id));
    buttons.set(theme.id, button);
    return button;
  }
  THEMES.forEach((theme) => cards.append(choice(theme)));
  const footer = document.createElement("div");
  footer.className = "theme-picker-footer";
  const status = document.createElement("p");
  status.className = "muted";
  status.setAttribute("role", "status");
  const saveDefault = document.createElement("button");
  saveDefault.type = "button";
  saveDefault.id = "theme-set-default";
  saveDefault.className = "primary";
  setText(saveDefault, uiText("设为默认主题"));
  saveDefault.addEventListener("click", () => {
    try {
      appearance.setDefaultTheme();
      error.hidden = true;
      setText(
        status,
        phrase("已将{p0}设为默认主题。", {
          p0: uiText(themeInfo(appearance.current.theme).name),
        }),
      );
    } catch {
      setText(
        error,
        uiText(
          "无法保存默认主题，请检查浏览器存储权限后重试。当前外观与未保存内容已保留。",
        ),
      );
      error.hidden = false;
    }
  });
  footer.append(status, saveDefault);
  dialog.append(heading, description, cards, error, footer);
  document.body.append(dialog);
  function update() {
    const id = appearance.current.theme,
      defaultId = appearance.saved.defaultTheme;
    for (const [key, button] of buttons) {
      button.setAttribute("aria-current", key === id ? "true" : "false");
      const marker = button.querySelector(".theme-choice-current");
      marker.style.visibility = key === id ? "visible" : "hidden";
      marker.setAttribute("aria-hidden", String(key !== id));
      const defaultMarker = button.querySelector(".theme-choice-default");
      defaultMarker.style.visibility = key === defaultId ? "visible" : "hidden";
      defaultMarker.setAttribute("aria-hidden", String(key !== defaultId));
    }
    saveDefault.disabled = id === defaultId;
    setText(
      status,
      phrase("默认：{p0}。仅适用于当前浏览器或桌面客户端。", {
        p0: uiText(themeInfo(defaultId).name),
      }),
    );
  }
  const unsubscribe = appearance.subscribe(update);
  dismiss.addEventListener("click", close);
  dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const controls = [
      ...dialog.querySelectorAll("button:not(:disabled)"),
    ].filter((node) => node.getClientRects().length);
    const first = controls[0],
      last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  });
  dialog.addEventListener("close", () => {
    trigger?.setAttribute("aria-expanded", "false");
    if (
      !dialog.open &&
      trigger?.isConnected &&
      !trigger.closest("[hidden],[inert]")
    )
      trigger.focus({ preventScroll: true });
    if (!dialog.open) restoreScroll();
  });
  // Require both ends of the gesture on the backdrop, not a drag out of a card.
  let backdrop = false;
  function outside(event) {
    const rect = dialog.getBoundingClientRect();
    return (
      event.target === dialog &&
      (event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom)
    );
  }
  dialog.addEventListener("pointerdown", (event) => {
    backdrop = outside(event);
  });
  dialog.addEventListener("click", (event) => {
    if (backdrop && outside(event)) close();
    backdrop = false;
  });
  const hide = () => close();
  window.addEventListener("pagehide", hide);
  return {
    open(source = document.activeElement) {
      if (destroyed || dialog.open) return;
      trigger = source;
      position = { x: window.scrollX, y: window.scrollY };
      error.hidden = true;
      update();
      dialog.showModal();
      trigger?.setAttribute("aria-expanded", "true");
      buttons.get(appearance.current.theme)?.focus({ preventScroll: true });
      restoreScroll();
    },
    close,
    destroy() {
      close();
      destroyed = true;
      unsubscribe();
      window.removeEventListener("pagehide", hide);
      dialog.remove();
    },
  };
}
