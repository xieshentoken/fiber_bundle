import { createBackupAutomation } from "./backup_automation.js";
import { phrase, uiText } from "./i18n/index.js";
// Offline intents contain no passphrase. Only the local terminal can execute them.
import { $, action, el, message } from "./ui.js";
import { control, recordHeading } from "./record_ui.js";
import { disclosure } from "./view_sections.js";
const labels = {
  prepared: "已准备，等待离线执行",
  encrypted_complete: "完成标记已记录，尚未校验备份",
  incomplete: "未完成，保留现场供核查",
  restored_locked: "已恢复并核验，等待单主机启用",
  restoring: "恢复进行中",
  failed: "恢复失败，禁止启动",
  active: "已在指定主机启用",
  retired: "源主机已停用",
};
export function createBackupsView(api) {
  let epoch = 0;
  const automation = createBackupAutomation(
    api,
    $("backup-automation-content"),
  );
  async function refresh() {
    const revision = ++epoch;
    const [items] = await Promise.all([
      $("backups-legacy").open ? api.backups() : [],
      automation.refresh(),
    ]);
    if (revision !== epoch) return;
    const root = $("backups-content");
    root.replaceChildren();
    const history = el("div");
    if (!items.length) root.append(el("p", uiText("暂无操作记录。")));
    for (const item of items) {
      const card = el("article", null, "row record");
      const tone = ["failed", "incomplete"].includes(item.state)
        ? "error"
        : item.state === "active"
          ? "success"
          : "waiting";
      card.append(
        recordHeading(
          item.action === "create" ? "完整数据备份" : "干净位置恢复",
          labels[item.state] || "状态未知，请核查",
          tone,
        ),
        el("p", phrase("位置：{p0}", { p0: item.destination })),
      );
      if (item.state === "prepared") {
        const command = el("pre"),
          code = el("code", item.command);
        command.append(code);
        card.append(
          el(
            "p",
            uiText(
              "先停止管理后台、微信接收及全部任务进程，再在终端执行以下命令。使用非默认配置时追加 --config 实际配置文件。",
            ),
          ),
          command,
          el(
            "p",
            uiText(
              "备份密码只在终端隐藏输入。请另行妥善保存，丢失后不能解密。",
            ),
          ),
        );
      }
      if (item.action === "create" && item.state === "encrypted_complete") {
        card.append(
          el(
            "p",
            uiText(
              "列表仅核对完成标记和文件元数据。完整哈希校验请在终端执行；恢复仍须验证密码和全部原件。",
            ),
          ),
        );
        if (item.verification_command) {
          const command = el("pre");
          command.append(el("code", item.verification_command));
          card.append(command);
        }
        card.append(
          control("准备干净恢复", async (notify) => {
            await api.prepareBackup("restore", item.id);
            await refresh();
            notify("恢复操作已准备；不会覆盖或启动现有项目。");
          }),
        );
      }
      if (item.state === "restored_locked")
        card.append(
          el(
            "p",
            uiText(
              "恢复保留费用与历史记录，客户端访问和待执行任务已停用。重配凭证并核对源机停用后，按恢复说明启用；不会自动发送或调用模型。",
            ),
          ),
        );
      (["active", "retired"].includes(item.state) ? history : root).append(
        card,
      );
    }
    if (history.children.length)
      root.append(disclosure("已完成与历史操作", history));
  }
  $("backups-legacy").addEventListener("toggle", () => {
    if ($("backups-legacy").open)
      refresh().catch((error) => message(error.message, true));
  });
  action($("backups-refresh"), refresh);
  action($("backups-prepare"), async (notify) => {
    await api.prepareBackup("create");
    await refresh();
    notify("备份操作已准备；离线执行后才会生成备份。");
  });
  return {
    refresh,
    clear() {
      epoch++;
      automation.clear();
      $("backups-content").replaceChildren();
    },
  };
}
