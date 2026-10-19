// Optional window geometry only; callers supply their profile path and current displays.
import { readFileSync, writeFileSync, renameSync } from "node:fs";
export function validBounds(value, areas) {
  if (!value || typeof value !== "object") return {};
  const bounds = Object.fromEntries(
    ["x", "y", "width", "height"].map((k) => [k, value[k]]),
  );
  if (
    !Object.values(bounds).every(Number.isSafeInteger) ||
    bounds.width < 760 ||
    bounds.height < 560
  )
    return {};
  const visible = areas.some(
    (a) =>
      bounds.x < a.x + a.width - 80 &&
      bounds.x + bounds.width > a.x + 80 &&
      bounds.y >= a.y - 20 &&
      bounds.y < a.y + a.height - 80,
  );
  return visible ? bounds : {};
}
export function loadBounds(path, areas) {
  try {
    return validBounds(JSON.parse(readFileSync(path, "utf8")), areas);
  } catch (error) {
    if (error.code !== "ENOENT")
      console.warn("Window geometry unavailable", error.code || "invalid_json");
    return {};
  }
}
export function saveBounds(path, value, areas) {
  const bounds = validBounds(value, areas);
  if (!Object.keys(bounds).length) return false;
  try {
    writeFileSync(path + ".tmp", JSON.stringify(bounds), { mode: 0o600 });
    renameSync(path + ".tmp", path);
    return true;
  } catch (error) {
    console.warn("Window geometry not saved", error.code || "write_failed");
    return false;
  }
}
