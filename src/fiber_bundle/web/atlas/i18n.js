// Bilingual text. Each module owns its strings next to the code that uses them;
// Chinese is the reference language and the fallback for a missing English entry.
import { locale, setLocale } from "../i18n/index.js";
export const LANGS = ["zh", "en"];
export function setLang(value) {
  setLocale(value === "en" ? "en" : "zh-CN");
  if (globalThis.document) document.documentElement.lang = locale();
}
export function lang() {
  return locale() === "en" ? "en" : "zh";
}
function fill(text, vars) {
  if (!vars || typeof text !== "string") return text;
  return text.replace(/\{(\w+)\}/g, (match, name) =>
    vars[name] === undefined || vars[name] === null
      ? match
      : String(vars[name]),
  );
}
// strings({ zh: { key: "…{n}…" }, en: { … } }) → t(key, vars)
export function strings(table) {
  return (key, vars) => {
    const text = table[lang()]?.[key] ?? table.zh?.[key];
    return text === undefined ? key : fill(text, vars);
  };
}
// Pick between inline variants: pick({ zh: "…", en: "…" }).
export function pick(variants) {
  return variants[lang()] ?? variants.zh;
}
