// Synchronous paint primer before the module graph loads. No writes or credentials.
// The controller performs full validation and font resolution once modules are ready.
try {
  const value = JSON.parse(window.localStorage.getItem("fiber.appearance.v1"));
  const root = document.documentElement;
  const themes = ["oracle", "tesla", "cursor", "clay", "dark", "atlas"];
  root.dataset.theme = themes.includes(value?.defaultTheme)
    ? value.defaultTheme
    : themes.includes(value?.theme)
      ? value.theme
      : "clay";
  if (value && typeof value === "object" && !Array.isArray(value)) {
    if (["comfortable", "compact"].includes(value.density))
      root.dataset.density = value.density;
    if (["system", "reduce"].includes(value.motion))
      root.dataset.motion = value.motion;
    if (
      Number.isInteger(value.latinSize) &&
      value.latinSize >= 12 &&
      value.latinSize <= 20
    )
      root.style.setProperty("--ui-base-size", `${value.latinSize}px`);
  }
} catch {
  document.documentElement.dataset.theme = "clay";
  /* Default tokens remain usable when storage is unavailable. */
}
