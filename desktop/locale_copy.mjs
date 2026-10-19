// Native and offline copy is packaged locally; no renderer HTML or business data.
const copy = {
  about: ["关于 Fiber bundle", "About Fiber bundle"],
  hide: ["隐藏 Fiber bundle", "Hide Fiber bundle"],
  hideOthers: ["隐藏其他", "Hide Others"],
  unhide: ["全部显示", "Show All"],
  reopen: ["重新打开管理窗口", "Reopen Admin Window"],
  reconnect: ["重新连接后台", "Reconnect Backend"],
  quit: [
    "退出管理应用（后台继续运行）",
    "Quit Admin App (Backend Keeps Running)",
  ],
  edit: ["编辑", "Edit"],
  undo: ["撤销", "Undo"],
  redo: ["重做", "Redo"],
  cut: ["剪切", "Cut"],
  copy: ["复制", "Copy"],
  paste: ["粘贴", "Paste"],
  selectAll: ["全选", "Select All"],
  view: ["查看", "View"],
  reload: ["重新载入界面", "Reload Interface"],
  zoomIn: ["放大", "Zoom In"],
  zoomOut: ["缩小", "Zoom Out"],
  resetZoom: ["恢复实际大小", "Actual Size"],
  fullscreen: ["切换全屏", "Toggle Full Screen"],
  window: ["窗口", "Window"],
  close: ["关闭管理窗口", "Close Admin Window"],
  minimize: ["最小化", "Minimize"],
  zoom: ["缩放窗口", "Zoom Window"],
  directory: ["选择文件夹", "Choose Folder"],
  chooseDirectory: ["选择此文件夹", "Choose This Folder"],
  unsavedTitle: ["未保存的内容", "Unsaved Changes"],
  unsavedMessage: [
    "当前页面有未保存的内容。",
    "This page has unsaved changes.",
  ],
  unsavedDetail: [
    "留在窗口可继续编辑；放弃后继续关闭或重新加载。已提交的后台操作不会因此取消。",
    "Stay to continue editing, or discard changes to close or reload. Submitted backend operations will not be canceled.",
  ],
  stay: ["留在窗口", "Stay"],
  discard: ["放弃并继续", "Discard and Continue"],
  offlineTitle: [
    "Fiber bundle · 后台连接失败",
    "Fiber bundle · Backend Disconnected",
  ],
  offlineHeading: ["无法连接本机后台", "Cannot connect to the local backend"],
  offlineSummary: [
    "管理窗口已打开，后台尚未就绪或已停止。",
    "The admin window is open, but the backend is not ready or has stopped.",
  ],
  offlineStart: [
    "在对应发行文件夹中双击“启动后台.command”。",
    "Double-click “启动后台.command” in the matching release folder.",
  ],
  offlineInstance: [
    "确认启动器与管理窗口使用相同实例和端口；自定义启动保留原有 --url 参数。",
    "Use the same instance and port for the launcher and admin window. Keep your original --url argument for a custom launch.",
  ],
  offlineNote: [
    "关闭此窗口不会启动、停止或重复运行后台任务。",
    "Closing this window does not start, stop, or duplicate backend tasks.",
  ],
};
export function desktopText(language, key) {
  if (!copy[key]) throw new Error(`unknown_desktop_text:${key}`);
  return copy[key][language === "en" ? 1 : 0];
}
export function retryText(language, seconds) {
  return language === "en"
    ? `Checking the connection again in ${seconds} seconds.`
    : `将在 ${seconds} 秒后重新检查连接。`;
}
export function menuTemplate(language, actions) {
  const t = (key) => desktopText(language, key),
    role = (name, key = name) => ({ role: name, label: t(key) }),
    separator = () => ({ type: "separator" });
  return [
    {
      label: "Fiber bundle",
      submenu: [
        role("about"),
        role("hide"),
        role("hideOthers"),
        role("unhide"),
        separator(),
        { label: t("reopen"), click: actions.open },
        { label: t("reconnect"), click: actions.connect },
        separator(),
        { label: t("quit"), accelerator: "CmdOrCtrl+Q", click: actions.quit },
      ],
    },
    {
      label: t("edit"),
      submenu: [
        role("undo"),
        role("redo"),
        separator(),
        role("cut"),
        role("copy"),
        role("paste"),
        role("selectAll"),
      ],
    },
    {
      label: t("view"),
      submenu: [
        role("reload"),
        separator(),
        role("zoomIn"),
        role("zoomOut"),
        role("resetZoom"),
        role("togglefullscreen", "fullscreen"),
      ],
    },
    {
      label: t("window"),
      submenu: [
        { label: t("close"), accelerator: "CmdOrCtrl+W", click: actions.close },
        role("minimize"),
        role("zoom"),
      ],
    },
  ];
}
