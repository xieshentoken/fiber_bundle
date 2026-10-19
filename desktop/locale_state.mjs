// Presentation-only locale mirror, scoped to one profile and backend origin.
import {
  constants,
  openSync,
  closeSync,
  fstatSync,
  readFileSync,
  writeFileSync,
  renameSync,
  unlinkSync,
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { join } from "node:path";
import { backendURL } from "./policy.mjs";
export function validLocale(value) {
  return Boolean(
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).join() === "language" &&
    ["en", "zh-CN"].includes(value.language),
  );
}
export function localePath(profile, backend) {
  const origin = new URL(backendURL(backend)).origin;
  return join(
    profile,
    `locale-${createHash("sha256").update(origin).digest("hex")}.json`,
  );
}
export function createLocaleState(profile, backend, warn = console.warn) {
  const origin = new URL(backendURL(backend)).origin,
    path = localePath(profile, backend);
  let language = "zh-CN",
    file;
  try {
    file = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const info = fstatSync(file);
    if (!info.isFile() || info.size > 4096 || info.nlink !== 1)
      throw new Error("invalid_cache");
    const cached = JSON.parse(readFileSync(file, "utf8"));
    if (
      Object.keys(cached).sort().join() !== "backendOrigin,language,version" ||
      cached.version !== 1 ||
      cached.backendOrigin !== origin ||
      !validLocale({ language: cached.language })
    )
      throw new Error("invalid_cache");
    language = cached.language;
  } catch (error) {
    if (error.code !== "ENOENT")
      warn("Desktop locale cache unavailable", error.code || "invalid_cache");
  } finally {
    if (file !== undefined) closeSync(file);
  }
  let saved = language;
  return Object.freeze({
    snapshot: () => ({ language }),
    apply(value) {
      if (!validLocale(value)) throw new Error("Invalid locale state");
      language = value.language;
      if (saved === language) return { language, cacheSaved: true };
      const temporary = `${path}.${randomUUID()}.tmp`;
      let handle;
      try {
        handle = openSync(temporary, "wx", 0o600);
        writeFileSync(
          handle,
          JSON.stringify({ version: 1, backendOrigin: origin, language }),
        );
        closeSync(handle);
        handle = undefined;
        renameSync(temporary, path);
        saved = language;
        return { language, cacheSaved: true };
      } catch (error) {
        warn("Desktop locale not saved", error.code || "write_failed");
        return { language, cacheSaved: false };
      } finally {
        if (handle !== undefined) closeSync(handle);
        try {
          unlinkSync(temporary);
        } catch (error) {
          if (error.code !== "ENOENT")
            warn(
              "Desktop locale temporary file not removed",
              error.code || "remove_failed",
            );
        }
      }
    },
  });
}
