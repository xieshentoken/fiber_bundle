// A presentation-only startup mirror; web preferences remain authoritative.
import {
  closeSync,
  fstatSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
  constants,
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { join } from "node:path";
import { backendURL } from "./policy.mjs";

const themes = new Set(["oracle", "tesla", "cursor", "clay", "dark", "atlas"]);
const defaultTheme = "clay";

export function validAppearance(value) {
  return Boolean(
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    ["defaultTheme,theme", "defaultTheme,presentationMode,theme"].includes(
      Object.keys(value).sort().join(),
    ) &&
    (value.presentationMode === undefined ||
      (value.theme === "atlas" &&
        ["light", "dark"].includes(value.presentationMode))) &&
    themes.has(value.theme) &&
    themes.has(value.defaultTheme),
  );
}

export function trustedAppearance(
  event,
  window,
  backend,
  offline,
  write = false,
) {
  if (
    !window ||
    event.sender !== window.webContents ||
    event.senderFrame !== window.webContents.mainFrame ||
    (event.senderFrame.url.split("#")[0] !== backend.split("#")[0] &&
      (write || event.senderFrame.url !== offline))
  )
    throw new Error("Untrusted appearance request");
}

export function themePresentation(theme, mode = "light") {
  if (!themes.has(theme)) throw new Error("Invalid presentation theme");
  return {
    themeSource:
      theme === "dark" || (theme === "atlas" && mode === "dark")
        ? "dark"
        : "light",
    backgroundColor: {
      atlas: mode === "dark" ? "#0e0e11" : "#f5f5f7",
      oracle: "#fbf9f8",
      tesla: "#ffffff",
      cursor: "#f7f7f4",
      clay: "#f5f5f3",
      dark: "#0b0b0a",
    }[theme],
  };
}

export function appearancePath(profile, backend) {
  const origin = new URL(backendURL(backend)).origin;
  const scope = createHash("sha256").update(origin).digest("hex");
  return join(profile, `appearance-${scope}.json`);
}

function loadDefault(path, origin, warn) {
  let file;
  try {
    file = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const info = fstatSync(file);
    if (!info.isFile() || info.size > 4096 || info.nlink !== 1)
      throw new Error("invalid_cache");
    const value = JSON.parse(readFileSync(file, "utf8"));
    if (
      !value ||
      ![
        "backendOrigin,defaultTheme,version",
        "backendOrigin,defaultTheme,presentationMode,version",
      ].includes(Object.keys(value).sort().join()) ||
      (value.presentationMode !== undefined &&
        (value.defaultTheme !== "atlas" ||
          !["light", "dark"].includes(value.presentationMode))) ||
      value.version !== 1 ||
      value.backendOrigin !== origin ||
      !themes.has(value.defaultTheme)
    )
      throw new Error("invalid_cache");
    return {
      theme: value.defaultTheme,
      mode: value.presentationMode || "light",
    };
  } catch (error) {
    if (error.code !== "ENOENT")
      warn(
        "Desktop appearance cache unavailable",
        error.code || "invalid_cache",
      );
    return { theme: defaultTheme, mode: "light" };
  } finally {
    if (file !== undefined) closeSync(file);
  }
}

function saveDefault(path, origin, theme, mode, warn) {
  const temporary = `${path}.${randomUUID()}.tmp`;
  let file;
  try {
    file = openSync(temporary, "wx", 0o600);
    writeFileSync(
      file,
      JSON.stringify({
        version: 1,
        backendOrigin: origin,
        defaultTheme: theme,
        ...(theme === "atlas" ? { presentationMode: mode } : {}),
      }),
    );
    closeSync(file);
    file = undefined;
    renameSync(temporary, path);
    return true;
  } catch (error) {
    warn("Desktop default theme not saved", error.code || "write_failed");
    return false;
  } finally {
    if (file !== undefined) closeSync(file);
    try {
      unlinkSync(temporary);
    } catch (error) {
      if (error.code !== "ENOENT")
        warn(
          "Desktop appearance temporary file not removed",
          error.code || "remove_failed",
        );
    }
  }
}

export function createAppearanceState(profile, backend, warn = console.warn) {
  const origin = new URL(backendURL(backend)).origin;
  const path = appearancePath(profile, backend);
  const initial = loadDefault(path, origin, warn);
  let saved = initial.theme,
    savedMode = initial.mode,
    current = saved,
    currentMode = savedMode;
  const snapshot = () => ({
    theme: current,
    defaultTheme: saved,
    ...(current === "atlas" ? { presentationMode: currentMode } : {}),
  });
  return Object.freeze({
    snapshot,
    apply(value) {
      if (!validAppearance(value)) throw new Error("Invalid appearance state");
      current = value.theme;
      currentMode = value.presentationMode || "light";
      const nextMode =
        value.defaultTheme === "atlas"
          ? current === "atlas"
            ? currentMode
            : savedMode
          : "light";
      const defaultSaved =
        (value.defaultTheme === saved && nextMode === savedMode) ||
        saveDefault(path, origin, value.defaultTheme, nextMode, warn);
      if (defaultSaved) {
        saved = value.defaultTheme;
        savedMode = nextMode;
      }
      return { ...snapshot(), defaultSaved };
    },
  });
}
