// Atlas display preferences. Only validated, non-sensitive choices reach browser storage.
export const PREFS_KEY = "fiber.atlas.v1";
export const UI_COOKIE = "fiber_ui";
const choices = {
  lang: ["zh", "en"],
  mode: ["system", "light", "dark"],
  density: ["comfortable", "compact"],
  motion: ["system", "reduce"],
  accent: ["iris", "teal", "amber", "graphite", "clay"],
};
export function defaultPrefs(language = globalThis.navigator?.language || "") {
  return {
    lang: /^zh/i.test(language) || !language ? "zh" : "en",
    mode: "system",
    density: "comfortable",
    motion: "system",
    accent: "iris",
  };
}
export function normalizePrefs(value, base = defaultPrefs()) {
  const result = { ...base };
  if (!value || typeof value !== "object" || Array.isArray(value))
    return result;
  for (const [name, allowed] of Object.entries(choices))
    if (allowed.includes(value[name])) result[name] = value[name];
  return result;
}
export function readPrefs(storage) {
  try {
    return normalizePrefs(JSON.parse(storage.getItem(PREFS_KEY)));
  } catch {
    return defaultPrefs();
  }
}
export function writePrefs(storage, value) {
  const clean = normalizePrefs(value);
  storage.setItem(PREFS_KEY, JSON.stringify(clean));
  return clean;
}
export function resolvedMode(mode, matchMedia = globalThis.matchMedia) {
  if (mode !== "system") return mode;
  return matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}
export function applyPrefs(root, prefs) {
  root.dataset.mode = resolvedMode(prefs.mode);
  root.dataset.density = prefs.density;
  root.dataset.motion = prefs.motion;
  root.dataset.accent = prefs.accent;
}
// The server picks the document for `/` from this cookie; any other value means classic.
export function uiCookie(value) {
  if (!["atlas", "classic"].includes(value)) throw new Error("unknown UI");
  return `${UI_COOKIE}=${value}; Path=/; Max-Age=31536000; SameSite=Strict`;
}
export function switchUi(value) {
  if (!["atlas", "classic"].includes(value)) throw new Error("unknown UI");
  import("../theme_entry.js").then(({ navigateTheme }) =>
    navigateTheme(value === "atlas" ? "atlas" : "clay"),
  );
}
