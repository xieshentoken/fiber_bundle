// Explicit single-file actions only. Status rendering never starts a timer or download.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { uiText, setText, phrase } from "./i18n/index.js";

const states = {
  available: "当前版本已在本机；请重新生成整理预览。",
  not_requested: "本地正文未就绪；系统尚未请求下载。",
  requested: "系统已接收下载请求，尚未确认本地正文就绪。",
  downloading: "iCloud 正在下载此文件。",
  conflict: "iCloud 存在版本冲突，请在 Finder 或 Obsidian 中核对。",
  error: "iCloud 返回下载错误，请在 Finder 中核对同步状态。",
  unknown: "系统状态不明；未请求下载。",
};
export function cloudRecoveryControls(path, scope, api) {
  const host = el("div", null, "cloud-recovery-actions"),
    status = el("p");
  const show = (value) =>
    setText(
      status,
      phrase("{p0}（检查于 {p1}）", {
        p0: uiText(states[value.state] || states.unknown),
        p1: new Date().toLocaleTimeString(),
      }),
    );
  host.append(
    control("检查此文件状态", async () =>
      show(await api.cloudFile({ ...scope, path })),
    ),
    control("下载此文件", async () => {
      const checked = await api.cloudFile({ ...scope, path });
      if (
        ["available", "requested", "downloading", "conflict"].includes(
          checked.state,
        )
      )
        return show(checked);
      show(
        await api.cloudFile({
          ...scope,
          path,
          action: "download",
          expected: checked.expected,
        }),
      );
    }),
  );
  if (window.fiberDesktop?.revealCloudNote)
    host.append(
      control("在 Finder 中定位", () =>
        window.fiberDesktop.revealCloudNote({
          clientId: scope.client_id,
          vaultId: scope.vault_id,
          path,
        }),
      ),
    );
  host.append(status);
  return host;
}
