// Fixed local themes. Business status colors and data are independent of brand styling.
export const DEFAULT_THEME = "clay";
export const THEMES = Object.freeze([
  Object.freeze({
    id: "oracle",
    name: "暖纸 · Oracle",
    description: "暖纸分区、炭灰操作与衬线展示标题",
    latin: "dm",
    cjk: "noto",
    displayLatin: "serif",
    displayCjk: "serif",
  }),
  Object.freeze({
    id: "tesla",
    name: "极简 · Tesla",
    description: "白灰空间、轻量层级与清晰的蓝色操作",
    latin: "system",
    cjk: "system",
    displayLatin: "system",
    displayCjk: "system",
  }),
  Object.freeze({
    id: "cursor",
    name: "暖米 · Cursor",
    description: "奶油底色、墨色文字与克制的橙色强调",
    latin: "dm",
    cjk: "noto",
    displayLatin: "dm",
    displayCjk: "noto",
  }),
  Object.freeze({
    id: "clay",
    name: "暖陶 · Clay",
    description: "暖灰底色、陶土操作与柔和分层阴影",
    latin: "dm",
    cjk: "noto",
    displayLatin: "dm",
    displayCjk: "noto",
  }),
  Object.freeze({
    id: "dark",
    name: "暗色",
    description: "近黑表面、冰蓝操作与柔和的状态色",
    latin: "dm",
    cjk: "noto",
    displayLatin: "dm",
    displayCjk: "noto",
  }),
]);
export const THEME_IDS = Object.freeze(THEMES.map(({ id }) => id));
export function themeInfo(id) {
  return (
    THEMES.find((theme) => theme.id === id) ||
    THEMES.find((theme) => theme.id === DEFAULT_THEME)
  );
}
export function themeFont(id, script, display = false) {
  const theme = themeInfo(id);
  return (
    theme[
      display ? (script === "latin" ? "displayLatin" : "displayCjk") : script
    ] || (script === "latin" ? "dm" : "noto")
  );
}
