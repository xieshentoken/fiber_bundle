import { h } from "./dom.js";
import { button, card } from "./ui.js";
import { THEMES } from "../themes.js";
import { navigateTheme } from "../theme_entry.js";
import { lang } from "./i18n.js";
import { translate } from "../i18n/index.js";
import { run, toast } from "./feedback.js";
import { sheet } from "./dialog.js";
export function themeChoices(ctx) {
  const en = lang() === "en",
    options = h("div.a-form-grid");
  for (const theme of THEMES)
    options.append(
      button(theme.id === "atlas" ? "Atlas" : translate(theme.name), {
        variant: theme.id === "atlas" ? "primary" : "ghost",
        onClick: () => {
          try {
            if (theme.id !== "atlas" && ctx.confirmReload())
              navigateTheme(theme.id);
          } catch {
            toast(
              en
                ? "Couldn't switch themes. The current page is kept."
                : "无法切换主题，当前页面已保留。",
              { tone: "error" },
            );
          }
        },
      }),
    );
  const save = button(en ? "Use Atlas by default" : "将 Atlas 设为默认主题", {
    onClick: () =>
      run(save, async () => {
        ctx.appearance.setDefaultTheme("atlas");
        toast(
          en ? "Atlas is now the default theme." : "Atlas 已设为默认主题。",
          { tone: "success" },
        );
      }),
  });
  return card({ title: en ? "Themes" : "主题" }, options, save);
}

export function openThemePicker(ctx, trigger) {
  if (document.querySelector("#theme-picker[open]")) return;
  const en = lang() === "en";
  const panel = sheet({
    title: en ? "Choose theme" : "选择主题",
    subtitle: en
      ? "Theme changes keep your startup default. Unsaved changes are checked before leaving."
      : "切换主题不会修改默认主题；离开前会检查未保存内容。",
    onClose: () => trigger?.setAttribute("aria-expanded", "false"),
  });
  panel.node.id = "theme-picker";
  panel.node.classList.add("a-theme-picker");
  panel.body.append(themeChoices(ctx));
  trigger?.setAttribute("aria-expanded", "true");
  return panel;
}
