// Presentation admission only. No credentials or business state cross a UI reload.
import { THEME_IDS } from "./themes.js";
import { readAppearance } from "./appearance_preferences.js";
const PENDING = "fiber.theme.navigation.v1";
export const themeRenderer = (id) => (id === "atlas" ? "atlas" : "classic");
export function entryTheme(local, session) {
  let pending;
  try {
    pending = session?.getItem(PENDING);
    session?.removeItem(PENDING);
  } catch {
    /* Defaults remain usable. */
  }
  return THEME_IDS.includes(pending)
    ? pending
    : readAppearance(local).defaultTheme;
}
export function navigateTheme(
  id,
  storage = window.sessionStorage,
  navigate = () => window.location.reload(),
) {
  if (!THEME_IDS.includes(id)) throw new Error("invalid_theme");
  // Publish only after storage succeeds: a failed admission preserves the current page.
  storage.setItem(PENDING, id);
  navigate();
}
