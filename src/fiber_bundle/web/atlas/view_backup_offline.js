// Backup & restore. This page only prepares offline intents (no passphrase); the local
// terminal executes them. A completion marker is not a restore test.
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  codeBlock,
  empty,
  failure,
  notice,
  skeleton,
  steps,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";

const t = strings({
  zh: {
    flow: ["准备", "停止后台", "终端执行", "核对"],
    how: "先停止管理后台、微信接收及全部任务进程，再在本机终端执行准备好的命令。备份密码只在终端隐藏输入；请另行妥善保存，丢失后不能解密。状态库和知识库分别加密；外部凭证与浏览器登录须重新配置。",
    prepare: "准备完整数据备份",
    prepareTitle: "准备一份完整数据备份？",
    prepareBody: "只会生成离线执行意图，不会立即备份或停止任何进程。",
    prepared: "备份操作已准备；离线执行后才会生成备份。",
    records: "备份与恢复记录",
    none: "暂无备份或恢复记录。",
    create: "完整数据备份",
    restore: "干净位置恢复",
    location: "位置：{v}",
    st_prepared: "已准备，等待离线执行",
    st_encrypted_complete: "完成标记已记录，尚未校验",
    st_incomplete: "未完成，保留现场供核查",
    st_restored_locked: "已恢复并核验，等待单主机启用",
    st_restoring: "恢复进行中",
    st_failed: "恢复失败，禁止启动",
    st_active: "已在指定主机启用",
    st_retired: "源主机已停用",
    st_other: "状态未知，请核查",
    runIt:
      "停止全部后台与任务后，在终端执行。使用非默认配置时追加 --config 实际配置文件。",
    verify:
      "列表仅核对完成标记和文件元数据。完整哈希校验请在终端执行；恢复仍须验证密码和全部原件。",
    prepareRestore: "准备干净恢复",
    restoreTitle: "准备干净位置恢复？",
    restoreBody: "恢复到新目录，默认停用；不会覆盖或启动现有项目。",
    restorePrepared: "恢复操作已准备；不会覆盖或启动现有项目。",
    locked:
      "恢复保留费用与历史记录，客户端访问和待执行任务已停用。重配凭证并核对源机停用后，按恢复说明启用；不会自动发送或调用模型。",
  },
  en: {
    flow: ["Prepare", "Stop backend", "Run in terminal", "Verify"],
    how: "Stop the admin backend, the WeChat receiver and all task processes, then run the prepared command in a local terminal. The backup password is typed hidden in the terminal only — store it safely; it can't be recovered. State and vaults are encrypted separately; external credentials and browser sign-ins must be configured again.",
    prepare: "Prepare full data backup",
    prepareTitle: "Prepare a full data backup?",
    prepareBody:
      "This only creates an offline intent; nothing is backed up or stopped yet.",
    prepared:
      "Backup prepared. The backup only exists after you run it offline.",
    records: "Backup and restore records",
    none: "No backup or restore records yet.",
    create: "Full data backup",
    restore: "Clean-location restore",
    location: "Location: {v}",
    st_prepared: "Prepared, waiting for offline run",
    st_encrypted_complete: "Completion marker recorded, not verified",
    st_incomplete: "Incomplete; state kept for inspection",
    st_restored_locked:
      "Restored and verified; waiting for single-host activation",
    st_restoring: "Restoring",
    st_failed: "Restore failed; start is blocked",
    st_active: "Active on the chosen host",
    st_retired: "Source host retired",
    st_other: "Unknown state — inspect",
    runIt:
      "After stopping every backend and task, run this in a terminal. Append --config with your actual config file if it isn't the default.",
    verify:
      "This list only checks the completion marker and file metadata. Run full hash verification in a terminal; a restore still verifies the password and every original.",
    prepareRestore: "Prepare clean restore",
    restoreTitle: "Prepare a clean-location restore?",
    restoreBody:
      "Restores into a new folder, disabled by default; the existing project is never overwritten or started.",
    restorePrepared:
      "Restore prepared; the existing project won't be overwritten or started.",
    locked:
      "The restore keeps costs and history; client access and pending tasks are disabled. Reconfigure credentials and confirm the source host is retired, then activate per the restore guide. Nothing sends or calls models automatically.",
  },
});
const tones = {
  failed: "error",
  incomplete: "error",
  active: "success",
  retired: "info",
};
const known = [
  "prepared",
  "encrypted_complete",
  "incomplete",
  "restored_locked",
  "restoring",
  "failed",
  "active",
  "retired",
];

export function createView(ctx) {
  const list = h("div.a-stack");
  let epoch = 0;
  async function refresh() {
    const mine = ++epoch;
    if (!list.childElementCount) clear(list, skeleton(3));
    let items;
    try {
      items = await ctx.api.backups();
    } catch (error) {
      if (mine === epoch) clear(list, failure(error, refresh));
      return;
    }
    if (mine !== epoch) return;
    clear(
      list,
      items.length
        ? items.map((item) => {
            const parts = [
              h("p.a-subtle", t("location", { v: item.destination })),
            ];
            if (item.state === "prepared")
              parts.push(
                notice(t("runIt"), "warning"),
                codeBlock(item.command),
              );
            if (
              item.action === "create" &&
              item.state === "encrypted_complete"
            ) {
              parts.push(notice(t("verify"), "info"));
              if (item.verification_command)
                parts.push(codeBlock(item.verification_command));
              const restore = button(t("prepareRestore"), {
                iconName: "refresh",
              });
              restore.addEventListener("click", () =>
                run(restore, async () => {
                  if (
                    !(await confirm({
                      title: t("restoreTitle"),
                      body: t("restoreBody"),
                      confirmLabel: t("prepareRestore"),
                    }))
                  )
                    return false;
                  await ctx.api.prepareBackup("restore", item.id);
                  toast(t("restorePrepared"), { tone: "success" });
                  await refresh();
                }),
              );
              parts.push(h("div.a-form-actions", restore));
            }
            if (item.state === "restored_locked")
              parts.push(notice(t("locked"), "info"));
            return card(
              {
                title: item.action === "create" ? t("create") : t("restore"),
                actions: badge(
                  known.includes(item.state)
                    ? t("st_" + item.state)
                    : t("st_other"),
                  tones[item.state] || "waiting",
                ),
              },
              ...parts,
            );
          })
        : card({}, empty(t("none"), { iconName: "backups" })),
    );
  }
  const prepare = button(t("prepare"), {
    variant: "primary",
    iconName: "backups",
  });
  prepare.addEventListener("click", () =>
    run(prepare, async () => {
      if (
        !(await confirm({
          title: t("prepareTitle"),
          body: t("prepareBody"),
          confirmLabel: t("prepare"),
        }))
      )
        return false;
      await ctx.api.prepareBackup("create");
      toast(t("prepared"), { tone: "success" });
      await refresh();
    }),
  );
  return {
    node: h(
      "div.a-stack",
      card({}, steps(t("flow"), 0), notice(t("how"), "warning")),
      h("h2", t("records")),
      list,
    ),
    header: { actions: [prepare] },
    refresh,
    dispose: () => epoch++,
  };
}
