// Validated local preferences and preview composition; no DOM or business requests.
import { localFont } from "./local_fonts.js";
import { THEME_IDS, DEFAULT_THEME } from "./themes.js";
export const APPEARANCE_KEY = "fiber.appearance.v1";
export const DEFAULT_APPEARANCE = Object.freeze({
  latinFont: "dm",
  cjkFont: "noto",
  latinSize: 15,
  cjkSize: 15,
  theme: DEFAULT_THEME,
  defaultTheme: DEFAULT_THEME,
  density: "comfortable",
  motion: "system",
  lineHeight: "normal",
});
const choices = {
  latinFont: ["dm", "system", "serif", "theme"],
  cjkFont: ["noto", "system", "serif", "theme"],
  density: ["comfortable", "compact"],
  motion: ["system", "reduce"],
  lineHeight: ["normal", "relaxed"],
};
export function normalizeAppearance(value) {
  const result = { ...DEFAULT_APPEARANCE };
  if (!value || typeof value !== "object" || Array.isArray(value))
    return result;
  for (const [name, allowed] of Object.entries(choices))
    if (allowed.includes(value[name])) result[name] = value[name];
  // Existing formal themes become the initial default; removed/invalid IDs fall back.
  result.defaultTheme = THEME_IDS.includes(value.defaultTheme)
    ? value.defaultTheme
    : THEME_IDS.includes(value.theme)
      ? value.theme
      : DEFAULT_THEME;
  result.theme = THEME_IDS.includes(value.theme)
    ? value.theme
    : result.defaultTheme;
  for (const name of ["latinFont", "cjkFont"])
    if (localFont(value[name])) result[name] = value[name];
  for (const name of ["latinSize", "cjkSize"])
    if (Number.isInteger(value[name]) && value[name] >= 12 && value[name] <= 20)
      result[name] = value[name];
  return result;
}
function latestAppearance(storage) {
  return normalizeAppearance(JSON.parse(storage.getItem(APPEARANCE_KEY)));
}
export function readAppearance(storage, fallback = DEFAULT_APPEARANCE) {
  try {
    return latestAppearance(storage);
  } catch {
    return { ...fallback };
  }
}
export function writeAppearance(storage, value) {
  const clean = normalizeAppearance(value);
  storage.setItem(APPEARANCE_KEY, JSON.stringify(clean));
  return clean;
}
// Font previews and the session theme never commit each other. Only the explicit
// default action persists a startup theme; other windows keep their session theme.
export function createAppearancePreferences(storage, render, events) {
  let saved = readAppearance(storage),
    preview = {},
    sessionTheme = saved.defaultTheme;
  const listeners = new Set();
  function current() {
    return { ...saved, ...preview, theme: sessionTheme };
  }
  function update() {
    const value = current();
    const ready = render(value);
    for (const listener of listeners)
      listener({ saved: { ...saved }, current: value });
    return ready;
  }
  function receive(value) {
    saved = value;
    return update();
  }
  function sync(event) {
    if (event.storageArea && event.storageArea !== storage) return;
    if (event.key !== APPEARANCE_KEY && event.key !== null) return;
    // Read latest storage, not a queued event's potentially stale value.
    receive(readAppearance(storage, saved));
  }
  events?.addEventListener("storage", sync);
  update();
  return {
    get saved() {
      return { ...saved };
    },
    get current() {
      return current();
    },
    apply(value) {
      const clean = normalizeAppearance(value);
      preview = Object.fromEntries(
        Object.entries(clean).filter(
          ([key, item]) =>
            key !== "theme" && key !== "defaultTheme" && item !== saved[key],
        ),
      );
      return update();
    },
    cancel() {
      preview = {};
      return update();
    },
    save(value) {
      const clean = normalizeAppearance({ ...saved, ...value });
      // Reread independent theme commits, including those from another window.
      const latest = latestAppearance(storage);
      const next = writeAppearance(storage, {
        ...clean,
        theme: latest.defaultTheme,
        defaultTheme: latest.defaultTheme,
      });
      for (const [key, item] of Object.entries(preview))
        if (item === next[key]) delete preview[key];
      saved = next;
      return update();
    },
    selectTheme(id) {
      if (!THEME_IDS.includes(id)) throw new Error("invalid_theme");
      sessionTheme = id;
      receive(readAppearance(storage, saved));
      return current();
    },
    setDefaultTheme(id = sessionTheme) {
      if (!THEME_IDS.includes(id)) throw new Error("invalid_theme");
      const latest = latestAppearance(storage);
      const next =
        latest.defaultTheme === id && latest.theme === id
          ? latest
          : writeAppearance(storage, {
              ...latest,
              theme: id,
              defaultTheme: id,
            });
      // Commit only after storage succeeds. Unsaved font previews remain previews.
      receive(next);
      return { ...saved };
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    destroy() {
      events?.removeEventListener("storage", sync);
      listeners.clear();
    },
  };
}
