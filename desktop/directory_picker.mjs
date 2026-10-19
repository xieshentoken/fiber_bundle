// One native chooser at a time; no business API or persistent settings are touched.
import { realpath, stat } from "node:fs/promises";
import { isAbsolute } from "node:path";

export function createDirectoryPicker({
  app,
  dialog,
  platform = process.platform,
  text,
}) {
  let pending = null;
  async function open(window, currentPath) {
    let defaultPath = app.getPath("home");
    if (currentPath) {
      try {
        const resolved = await realpath(currentPath);
        if ((await stat(resolved)).isDirectory()) defaultPath = resolved;
      } catch {
        // A stale typed path must not prevent choosing an existing directory.
      }
    }
    if (window.isDestroyed()) throw new Error("directory_picker_closed");
    if (window.isMinimized()) window.restore();
    window.show();
    if (platform === "darwin") app.focus({ steal: true });
    window.focus();
    const options = {
      title: text("directory"),
      buttonLabel: text("chooseDirectory"),
      defaultPath,
      properties: ["openDirectory"],
    };
    // Keep an asynchronous sheet attached to the now active, visible owner.
    const result = await dialog.showOpenDialog(window, options);
    if (result?.canceled) return null;
    if (
      !result ||
      !Array.isArray(result.filePaths) ||
      result.filePaths.length !== 1
    )
      throw new Error("directory_picker_invalid_result");
    const selected = result.filePaths[0];
    if (
      typeof selected !== "string" ||
      !isAbsolute(selected) ||
      selected.includes("\0")
    )
      throw new Error("directory_picker_invalid_result");
    const path = await realpath(selected);
    if (!(await stat(path)).isDirectory())
      throw new Error("directory_picker_invalid_result");
    return window.isDestroyed() ? null : path;
  }
  return function choose(window, currentPath = "") {
    if (
      typeof currentPath !== "string" ||
      currentPath.length > 4096 ||
      currentPath.includes("\0") ||
      (currentPath && !isAbsolute(currentPath))
    )
      throw new Error("directory_picker_invalid_path");
    if (pending) {
      if (platform === "darwin") app.focus({ steal: true });
      throw new Error("directory_picker_busy");
    }
    const request = open(window, currentPath);
    pending = request;
    return request.finally(() => {
      if (pending === request) pending = null;
      if (!window.isDestroyed()) window.focus();
    });
  };
}
