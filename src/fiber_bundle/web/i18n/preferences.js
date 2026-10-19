// Language is client-local and independent of appearance and business settings.
export const LOCALE_KEY = "fiber.locale.v1";
export const normalizeLocale = (value) => (value === "en" ? "en" : "zh-CN");
export function createLocalePreferences(storage) {
  let current = "zh-CN";
  try {
    current = normalizeLocale(storage?.getItem(LOCALE_KEY));
  } catch {
    /* Keep the last known language. */
  }
  return {
    get current() {
      return current;
    },
    select(value) {
      const next = normalizeLocale(value);
      // Publish only after the preference is durable. A failed save preserves UI and drafts.
      storage.setItem(LOCALE_KEY, next);
      current = next;
      return current;
    },
  };
}
