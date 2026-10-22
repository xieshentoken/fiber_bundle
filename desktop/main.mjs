// Desktop owns windows only. Background processes and business data remain behind HTTP.
import {
  app,
  BrowserWindow,
  Menu,
  session,
  dialog,
  ipcMain,
  shell,
  screen,
  nativeTheme,
} from "electron";
import { setTimeout, clearTimeout } from "node:timers";
import { loadBounds, saveBounds } from "./window_state.mjs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  backendURL,
  allowedRequest,
  allowedDownload,
  windowPreferences,
} from "./policy.mjs";

import { createDirectoryPicker } from "./directory_picker.mjs";
import { trusted, openNote, trashIntent } from "./native.mjs";
import {
  createAppearanceState,
  themePresentation,
  trustedAppearance,
} from "./appearance_state.mjs";

import { createLocaleState } from "./locale_state.mjs";
import { menuTemplate, desktopText } from "./locale_copy.mjs";

const directory = dirname(fileURLToPath(import.meta.url));
const value = (key) => {
  const index = process.argv.indexOf(key);
  return index < 0 ? undefined : process.argv[index + 1];
};
const backend = backendURL(value("--url"));
const data = value("--user-data");
if (data) app.setPath("userData", resolve(data));
app.setName("Fiber bundle");
let window = null;
let appearance = null;
let language = null;
const nativeText = (key) =>
  desktopText(language?.snapshot().language || "zh-CN", key);
function updateMenu() {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate(
      menuTemplate(language.snapshot().language, {
        open: openWindow,
        connect,
        quit: () => app.quit(),
        close: () => window?.close(),
      }),
    ),
  );
}
const offline = pathToFileURL(join(directory, "offline.html")).href;
const localFiles = [
  "offline.html",
  "offline.js",
  "locale_copy.mjs",
  "offline.css",
  "tokens.css",
  "themes.css",
  "fonts.css",
  "dm-sans.ttf",
  "noto-sans-sc.ttf",
  "logo-original.png",
].map((name) => pathToFileURL(join(directory, name)).href);

const chooseDirectory = createDirectoryPicker({
  app,
  dialog,
  text: nativeText,
});
ipcMain.handle("fiber:select-directory", async (event, currentPath) => {
  const target = window;
  trusted(event, target, backend);
  const result = await chooseDirectory(target, currentPath);
  trusted(event, window, backend);
  return result;
});

ipcMain.handle("fiber:open-note", async (event, value) => {
  trusted(event, window, backend);
  return openNote(window, backend, value, shell);
});
ipcMain.handle("fiber:trash-intent", async (event, value) => {
  trusted(event, window, backend);
  return trashIntent(window, backend, value);
});
ipcMain.handle("fiber:appearance-snapshot", (event) => {
  trustedAppearance(event, window, backend, offline);
  return appearance.snapshot();
});
ipcMain.handle("fiber:apply-appearance", (event, value) => {
  trustedAppearance(event, window, backend, offline, true);
  const result = appearance.apply(value);
  const presentation = themePresentation(result.theme, result.presentationMode);
  nativeTheme.themeSource = presentation.themeSource;
  window.setBackgroundColor(presentation.backgroundColor);
  return result;
});

ipcMain.handle("fiber:locale-snapshot", (event) => {
  trustedAppearance(event, window, backend, offline);
  return language.snapshot();
});
ipcMain.handle("fiber:apply-locale", (event, value) => {
  trustedAppearance(event, window, backend, offline, true);
  const result = language.apply(value);
  updateMenu();
  return result;
});

function connect() {
  if (!window || window.isDestroyed()) return openWindow();
  const target = window;
  target.loadURL(backend).catch((error) => {
    if (error.code === "ERR_ABORTED") return;
    if (window === target && !target.isDestroyed()) target.loadURL(offline);
  });
}
function openWindow() {
  if (window && !window.isDestroyed()) {
    window.show();
    window.focus();
    return;
  }
  const presentation = themePresentation(
    appearance.snapshot().theme,
    appearance.snapshot().presentationMode,
  );
  nativeTheme.themeSource = presentation.themeSource;
  window = new BrowserWindow({
    width: 1180,
    height: 820,
    ...loadBounds(
      join(app.getPath("userData"), "window-state.json"),
      screen.getAllDisplays().map((d) => d.workArea),
    ),
    show: false,
    backgroundColor: presentation.backgroundColor,
    minWidth: 760,
    minHeight: 560,
    title: "Fiber bundle",
    webPreferences: {
      ...windowPreferences,
      preload: join(directory, "preload.cjs"),
    },
  });
  const target = window;
  const reveal = () => {
    if (!target.isDestroyed()) target.show();
  };
  const revealTimer = setTimeout(reveal, 3000);
  target.once("ready-to-show", () => {
    clearTimeout(revealTimer);
    reveal();
  });
  target.on("close", () => {
    if (
      !target.isDestroyed() &&
      !target.isMinimized() &&
      !target.isFullScreen()
    )
      saveBounds(
        join(app.getPath("userData"), "window-state.json"),
        target.getNormalBounds(),
        screen.getAllDisplays().map((d) => d.workArea),
      );
  });
  target.once("closed", () => clearTimeout(revealTimer));
  target.webContents.on("will-prevent-unload", (event) => {
    const response = dialog.showMessageBoxSync(target, {
      type: "question",
      title: nativeText("unsavedTitle"),
      message: nativeText("unsavedMessage"),
      detail: nativeText("unsavedDetail"),
      buttons: [nativeText("stay"), nativeText("discard")],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    // Electron's event prevents the renderer veto, allowing the requested unload.
    if (response === 1) event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (
      url === "fiber://reconnect" &&
      window.webContents.getURL() === offline
    ) {
      event.preventDefault();
      connect();
    } else if (allowedDownload(url, backend)) {
      event.preventDefault();
      target.webContents.downloadURL(url);
    } else if (url !== backend && url !== offline) event.preventDefault();
  });
  window.webContents.on("will-redirect", (event, url) => {
    if (url !== backend) event.preventDefault();
  });
  window.webContents.on("will-attach-webview", (event) =>
    event.preventDefault(),
  );
  window.on("closed", () => {
    window = null;
  });
  connect();
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", openWindow);
  app.on("activate", openWindow);
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
  app.whenReady().then(() => {
    appearance = createAppearanceState(app.getPath("userData"), backend);
    language = createLocaleState(app.getPath("userData"), backend);
    const owned = session.fromPartition(windowPreferences.partition);
    owned.setPermissionRequestHandler((_web, _permission, callback) =>
      callback(false),
    );
    owned.setPermissionCheckHandler(() => false);
    owned.webRequest.onBeforeRequest((details, callback) =>
      callback({ cancel: !allowedRequest(details.url, backend, localFiles) }),
    );
    updateMenu();
    openWindow();
  });
}
