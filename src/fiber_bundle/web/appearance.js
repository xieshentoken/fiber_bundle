// DOM/font application only; preference transactions live in appearance_preferences.
import { localFont, createLocalFontFaces } from "./local_fonts.js";
import { createDesktopAppearanceSync } from "./desktop_appearance.js";
import { themeFont } from "./themes.js";
import {
  createAppearancePreferences,
  normalizeAppearance,
} from "./appearance_preferences.js";
export {
  APPEARANCE_KEY,
  DEFAULT_APPEARANCE,
  normalizeAppearance,
  readAppearance,
  writeAppearance,
} from "./appearance_preferences.js";
export function createAppearance({
  onWarning = (error) =>
    console.warn("Desktop appearance sync unavailable", error),
} = {}) {
  const syncDesktop = createDesktopAppearanceSync(
    window.fiberDesktop,
    onWarning,
  );
  let revision = 0;
  const applyLocal = createLocalFontFaces();
  const faces = new Map(),
    visited = new Set();
  function collect(sheet) {
    if (!sheet || visited.has(sheet)) return;
    visited.add(sheet);
    for (const rule of sheet.cssRules) {
      if (rule.styleSheet) collect(rule.styleSheet);
      if (rule.type === window.CSSRule.FONT_FACE_RULE) {
        const family = rule.style
          .getPropertyValue("font-family")
          .replace(/["']/g, "");
        if (family.startsWith("FiberCJK-")) faces.set(family, rule.style);
      }
    }
  }
  // Mutate descriptors on the packaged same-origin stylesheet; keep CSP style-src 'self'.
  for (const sheet of document.styleSheets) collect(sheet);
  async function apply(value) {
    const clean = normalizeAppearance(value),
      current = ++revision;
    const root = document.documentElement;
    root.dataset.theme = clean.theme;
    syncDesktop(clean);
    root.dataset.density = clean.density;
    root.dataset.motion = clean.motion;
    root.style.setProperty("--ui-base-size", `${clean.latinSize}px`);
    const lineScale = Math.max(1, clean.cjkSize / clean.latinSize);
    root.style.setProperty("--script-line-scale", String(lineScale));
    root.style.setProperty(
      "--ui-line-height",
      String((clean.lineHeight === "relaxed" ? 1.85 : 1.571429) * lineScale),
    );
    const latinChoice =
        clean.latinFont === "theme"
          ? themeFont(clean.theme, "latin")
          : clean.latinFont,
      cjkChoice =
        clean.cjkFont === "theme"
          ? themeFont(clean.theme, "cjk")
          : clean.cjkFont;
    const localLatin = applyLocal("latin", latinChoice),
      localCjk = applyLocal("cjk", cjkChoice, clean.cjkSize / clean.latinSize);
    const latin = localLatin.family
      ? `"${localLatin.family}", "DM Sans"`
      : {
          dm: '"DM Sans"',
          system: '-apple-system, BlinkMacSystemFont, "Segoe UI"',
          serif: 'Georgia, "Times New Roman"',
        }[latinChoice];
    // unicode-range and size-adjust keep Chinese and Latin sizes independent in mixed text.
    const name =
      localCjk.family ||
      (localFont(cjkChoice) ? "FiberCJK-noto" : `FiberCJK-${cjkChoice}`);
    for (const face of faces.values())
      face.setProperty(
        "size-adjust",
        `${(clean.cjkSize / clean.latinSize) * 100}%`,
      );
    const family = (cjk, latin) =>
      `"${cjk}", "FiberCJK-noto", ${latin}, "Noto Sans SC", sans-serif`;
    root.style.setProperty("--font-ui", family(name, latin || '"DM Sans"'));
    const displayLatin =
      clean.latinFont === "theme" &&
      themeFont(clean.theme, "latin", true) === "serif"
        ? 'Georgia, "Times New Roman"'
        : latin;
    const displayCjk =
      clean.cjkFont === "theme"
        ? `FiberCJK-${themeFont(clean.theme, "cjk", true)}`
        : name;
    root.style.setProperty(
      "--font-heading",
      family(displayCjk, displayLatin || '"DM Sans"'),
    );
    root.dataset.fontReady = "false";
    try {
      await Promise.all([
        localLatin.ready,
        localCjk.ready,
        document.fonts.load(`400 ${clean.latinSize}px "${name}"`, "中文预览"),
        document.fonts.load(
          `400 ${clean.latinSize}px "${displayCjk}"`,
          "中文标题",
        ),
      ]);
    } catch {
      /* The packaged fallback remains usable if a system face is unavailable. */
    }
    if (current !== revision) return clean;
    root.dataset.fontReady = "true";
    if (clean.motion === "reduce")
      document.getAnimations?.().forEach((animation) => animation.cancel());
    return clean;
  }
  let storage;
  try {
    storage = window.localStorage;
  } catch {
    storage = {
      getItem: () => null,
      setItem() {
        throw new Error("storage_unavailable");
      },
    };
  }
  return createAppearancePreferences(storage, apply, window);
}
