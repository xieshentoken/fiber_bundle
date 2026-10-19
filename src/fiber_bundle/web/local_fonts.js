// Font metadata and local CSS sources only. Font files never leave this client.
const prefix = "local:";
const cjkRange =
  "U+2E80-9FFF,U+F900-FAFF,U+FE10-FE6F,U+FF00-FFEF,U+20000-3134F";
function validName(value) {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= 256 &&
    // eslint-disable-next-line no-control-regex -- Font names cannot contain control characters.
    !/[\u0000-\u001f\u007f]/u.test(value)
  );
}
export function localFont(value) {
  if (
    typeof value !== "string" ||
    !value.startsWith(prefix) ||
    value.length > 1100
  )
    return null;
  try {
    const names = JSON.parse(value.slice(prefix.length));
    return Array.isArray(names) && names.length === 2 && names.every(validName)
      ? { family: names[0], postscriptName: names[1] }
      : null;
  } catch {
    return null;
  }
}
export function fontCatalog(records) {
  if (!Array.isArray(records) || records.length > 16384)
    throw new Error("invalid_font_catalog");
  const families = new Map();
  for (const record of records) {
    if (
      !record ||
      !validName(record.family) ||
      !validName(record.postscriptName) ||
      record.family.startsWith(".")
    )
      continue;
    const style = typeof record.style === "string" ? record.style : "";
    const score = /italic|oblique|斜/i.test(style)
      ? 100
      : /^(regular|normal|roman|常规|標準|标准)$/i.test(style)
        ? 0
        : /book/i.test(style)
          ? 1
          : /medium/i.test(style)
            ? 2
            : 10;
    const previous = families.get(record.family);
    if (!previous || score < previous.score)
      families.set(record.family, {
        family: record.family,
        postscriptName: record.postscriptName,
        value: prefix + JSON.stringify([record.family, record.postscriptName]),
        score,
      });
  }
  return [...families.values()].sort((a, b) =>
    a.family.localeCompare(b.family, "zh-CN", { numeric: true }),
  );
}

// Two bounded aliases preserve independent CJK sizing and packaged glyph fallback.
export function createLocalFontFaces() {
  const active = new Map();
  return function apply(script, value, ratio = 1) {
    const font = localFont(value),
      previous = active.get(script);
    // Chromium caches glyph metrics after load; a changed size needs a fresh face.
    if (previous?.value !== value || previous?.ratio !== ratio) {
      if (previous) document.fonts.delete(previous.face);
      active.delete(script);
    }
    if (!font || typeof window.FontFace !== "function")
      return { family: null, ready: Promise.resolve(false) };
    const family = script === "cjk" ? "FiberCJK-local" : "FiberLatin-local";
    let entry = active.get(script);
    try {
      if (!entry) {
        const face = new window.FontFace(
          family,
          `local(${JSON.stringify(font.postscriptName)})`,
          {
            display: "swap",
            ...(script === "cjk"
              ? { unicodeRange: cjkRange, sizeAdjust: `${ratio * 100}%` }
              : {}),
          },
        );
        entry = { value, ratio, face };
        active.set(script, entry);
        document.fonts.add(face);
      }
      const ready = entry.face.load().then(
        () => true,
        () => {
          document.fonts.delete(entry.face);
          if (active.get(script) === entry) active.delete(script);
          return false;
        },
      );
      return { family, ready };
    } catch {
      if (entry) document.fonts.delete(entry.face);
      active.delete(script);
      return { family: null, ready: Promise.resolve(false) };
    }
  };
}
