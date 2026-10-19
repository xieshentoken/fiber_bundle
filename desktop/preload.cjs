// Only trusted local top-level documents receive this constrained native capability.
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld(
  "fiberDesktop",
  Object.freeze({
    openNote: (value) => ipcRenderer.invoke("fiber:open-note", value),
    trashIntent: (value) => ipcRenderer.invoke("fiber:trash-intent", value),
    selectDirectory: (currentPath) =>
      ipcRenderer.invoke("fiber:select-directory", currentPath),
    localeSnapshot: () => ipcRenderer.invoke("fiber:locale-snapshot"),
    applyLocale: (value) => ipcRenderer.invoke("fiber:apply-locale", value),
    appearanceSnapshot: () => ipcRenderer.invoke("fiber:appearance-snapshot"),
    applyAppearance: (value) =>
      ipcRenderer.invoke("fiber:apply-appearance", value),
  }),
);
