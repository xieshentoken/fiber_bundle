import { locale, setLocale } from "../i18n/index.js";

// Preferences/navigation preparation must succeed before any visible text changes.
export function switchLocale(next, prepare, reload) {
  const previous = locale();
  prepare();
  setLocale(next === "en" ? "en" : "zh-CN");
  try {
    reload();
  } catch (error) {
    setLocale(previous);
    throw error;
  }
}
