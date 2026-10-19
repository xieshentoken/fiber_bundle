// Explicit message bindings update presentation in place, without feature reloads.
import { messages } from "./catalog.js";
import { createLocalePreferences, normalizeLocale } from "./preferences.js";
let storage;
try {
  storage = globalThis.localStorage;
} catch {
  /* Preference writes will report failure. */
}
const preferences = createLocalePreferences(storage);
let language = preferences.current;
const listeners = new Set(),
  bindings = new Set(),
  owned = new WeakMap();
const released = new FinalizationRegistry((binding) =>
  bindings.delete(binding),
);
const sources = new Map(
  Object.entries(messages).map(([key, value]) => [value[0], key]),
);
const patterns = [];
const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
for (const [key, [source]] of Object.entries(messages)) {
  if (!/\{p\d+\}/.test(source)) continue;
  const names = [...source.matchAll(/\{(p\d+)\}/g)].map((m) => m[1]);
  patterns.push({
    key,
    names,
    expression: new RegExp(
      "^" +
        source
          .split(/\{p\d+\}/)
          .map(escape)
          .join("([\\s\\S]*?)") +
        "$",
    ),
  });
}
patterns.sort(
  (a, b) =>
    messages[b.key][0].replace(/\{p\d+\}/g, "").length -
    messages[a.key][0].replace(/\{p\d+\}/g, "").length,
);
export function locale() {
  return language;
}
export function text(key, parameters = {}) {
  const pair = messages[key];
  if (!pair) throw new Error(`unknown_locale_key:${key}`);
  const source = pair[language === "en" ? 1 : 0];
  return source.replace(/\{([^}]+)\}/g, (match, name) =>
    Object.hasOwn(parameters, name) ? String(parameters[name]) : match,
  );
}
class Message {
  constructor(key, parameters) {
    this.key = key;
    this.parameters = parameters;
  }
  toString() {
    return text(this.key, this.parameters);
  }
  toJSON() {
    throw new Error("ui_message_not_business_data");
  }
}
export function msg(key, parameters = {}) {
  return new Message(key, parameters);
}
// Callers explicitly mark nested UI values; source names and paths stay raw.
export function phrase(source, parameters = {}) {
  const key = sources.get(source);
  if (!key) throw new Error(`unknown_locale_source:${source}`);
  return msg(key, parameters);
}
class JoinedMessage extends Message {
  constructor(parts, separator) {
    super(null, {});
    this.parts = parts;
    this.separator = separator;
  }
  toString() {
    return this.parts.map(String).join(this.separator);
  }
}
export function joinText(parts, separator = "") {
  return new JoinedMessage(parts, separator);
}
// Explicit raw-content marker for shared UI helpers that also accept UI copy.
export function sourceText(value) {
  return joinText([String(value ?? "")]);
}
class DateMessage extends Message {
  constructor(value, options) {
    super(null, {});
    this.date = new Date(value);
    this.options = options;
  }
  toString() {
    return new Intl.DateTimeFormat(
      language === "en" ? "en-US" : "zh-CN",
      this.options,
    ).format(this.date);
  }
}
export function localizedDate(value, options) {
  return new DateMessage(value, options);
}
export function languageAliases(value) {
  const key = sources.get(value);
  return key ? messages[key].join(" ") : String(value ?? "");
}
export function optionText(label, value) {
  const option = document.createElement("option");
  setText(option, uiText(label));
  option.value = value;
  return option;
}
// Compatibility at explicitly UI-owned boundaries; never scan arbitrary DOM/data.
export function uiText(value) {
  if (value instanceof Message || typeof value !== "string") return value;
  const key = sources.get(value);
  if (key) return msg(key);
  for (const item of patterns) {
    const match = item.expression.exec(value);
    if (match)
      return msg(
        item.key,
        Object.fromEntries(item.names.map((name, i) => [name, match[i + 1]])),
      );
  }
  return value;
}
export function translate(value) {
  return String(uiText(value) ?? "");
}
function write(node, value, attribute) {
  const rendered = String(value ?? "");
  if (attribute) node.setAttribute(attribute, rendered);
  else node.textContent = rendered;
  return rendered;
}
export function setText(node, value, attribute = "") {
  let slots = owned.get(node);
  if (!slots) {
    slots = new Map();
    owned.set(node, slots);
  }
  const previous = slots.get(attribute);
  if (previous) {
    bindings.delete(previous);
    released.unregister(previous);
  }
  const rendered = write(node, value, attribute);
  if (!(value instanceof Message)) {
    slots.delete(attribute);
    return;
  }
  const binding = { target: new WeakRef(node), value, attribute, rendered };
  slots.set(attribute, binding);
  bindings.add(binding);
  released.register(node, binding, binding);
}
export function textNode(value) {
  const node = document.createTextNode("");
  setText(node, value);
  return node;
}
export function onLocaleChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export function setLocale(value) {
  const next = normalizeLocale(value);
  if (next === language) return language;
  preferences.select(next);
  language = next;
  for (const binding of bindings) {
    const node = binding.target.deref();
    if (!node) {
      bindings.delete(binding);
      continue;
    }
    // A later feature/user update supersedes an older presentation binding.
    const current = binding.attribute
      ? node.getAttribute(binding.attribute)
      : node.textContent;
    if (current !== binding.rendered) {
      bindings.delete(binding);
      continue;
    }
    binding.rendered = write(node, binding.value, binding.attribute);
  }
  if (globalThis.document) document.documentElement.lang = language;
  for (const listener of listeners) listener(language);
  return language;
}
export function bindStatic(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    // Navigation owns its label span and icons after decoration.
    if (!node.hasAttribute("data-tab")) setText(node, msg(node.dataset.i18n));
  }
  for (const attribute of ["aria-label", "placeholder", "title"]) {
    for (const node of root.querySelectorAll(`[data-i18n-${attribute}]`))
      setText(
        node,
        msg(node.getAttribute(`data-i18n-${attribute}`)),
        attribute,
      );
  }
  document.documentElement.lang = language;
}
