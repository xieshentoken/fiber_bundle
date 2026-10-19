// Backup policy editing is separate from legacy offline recovery intents.
import { fillDirectory } from "./native_directory_picker.js";
import { locale } from "./i18n/index.js";
import { el, message } from "./ui.js";
import { control } from "./record_ui.js";
import { createBackupJobStatus, errorText } from "./backup_job_status.js";
import { createBackupPolling } from "./backup_polling.js";
const text = (zh, en) => (locale() === "en" ? en : zh);
async function operation(work) {
  try {
    return await work();
  } catch (error) {
    throw new Error(errorText(error.code) || error.message, { cause: error });
  }
}
export function createBackupAutomation(api, root) {
  let current = null;
  let epoch = 0;
  let statusRoot = null;
  const renderStatus = createBackupJobStatus({ api, operation, refresh });
  const polling = createBackupPolling({
    read: () => api.backupAutomation(),
    visible: () =>
      !document.hidden &&
      root.isConnected !== false &&
      !root.closest?.("[hidden]"),
    onValue: (value) => {
      if (statusRoot) renderStatus(value, statusRoot);
    },
    onError: () => {
      if (statusRoot) {
        const note = statusRoot.querySelector?.("[data-backup-read-error]");
        if (!note) {
          const item = el(
            "p",
            text(
              "状态读取暂时失败，将自动重试读取；不会重复提交备份。",
              "Status could not be read. Reading will retry without submitting another backup.",
            ),
            "error",
          );
          item.dataset.backupReadError = "true";
          statusRoot.append(item);
        }
      }
    },
  });
  let readDraft = null;
  function field(parent, key, zh, en, type = "number") {
    const label = el("label", text(zh, en));
    const input = el("input");
    input.type = type;
    input.name = key;
    input.id = "backup-policy-" + key;
    if (type === "checkbox") input.checked = current.policy[key];
    else input.value = current.policy[key];
    if (type === "number") input.min = "0";
    label.append(input);
    parent.append(label);
    return input;
  }
  async function refresh() {
    polling.stop();
    const draft = readDraft?.();
    const revision = ++epoch;
    const value = await api.backupAutomation();
    if (revision !== epoch) return;
    current = value;
    root.replaceChildren();
    const form = el("form", null, "backup-policy");
    form.append(
      el("h3", text("加密备份", "Encrypted backups")),
      el(
        "p",
        text(
          "目录和策略由你配置。后台运行时执行；恢复只写入新目录。",
          "Choose your folder and policies. Backups run while the backend is running. Restore only into a new folder.",
        ),
      ),
    );
    const directory = field(
      form,
      "directory",
      "备份文件夹",
      "Backup folder",
      "text",
    );
    directory.placeholder = text(
      "选择本机或已挂载磁盘上的目录",
      "A folder on this Mac or a mounted disk",
    );
    if (window.fiberDesktop?.selectDirectory)
      form.append(
        control(
          text("选择文件夹…", "Choose folder…"),
          () => fillDirectory(directory),
          { localize: false, restoreFocus: true, feedback: "directory" },
        ),
      );
    const keyLabel = el(
      "label",
      text(
        "恢复密钥（64 位十六进制，另行安全保存）",
        "Recovery key (64 hex characters; keep a separate safe copy)",
      ),
    );
    const key = el("input");
    key.type = "password";
    key.autocomplete = "new-password";
    key.id = "backup-recovery-key";
    key.placeholder = current.key_configured
      ? text("已保存；留空保留", "Saved; leave blank to keep")
      : text("尚未配置", "Not configured");
    keyLabel.append(key);
    form.append(keyLabel);
    form.append(
      control(
        text("生成恢复密钥", "Generate recovery key"),
        () => {
          const data = window.crypto.getRandomValues(new Uint8Array(32));
          key.value = Array.from(data, (v) =>
            v.toString(16).padStart(2, "0"),
          ).join("");
          key.type = "text";
          key.focus();
          key.select();
        },
        { localize: false },
      ),
    );
    const savedLabel = el(
      "label",
      text(
        "我已将恢复密钥保存在应用之外",
        "I have saved the recovery key outside this app",
      ),
    );
    const saved = el("input");
    saved.type = "checkbox";
    saved.id = "backup-key-saved";
    savedLabel.append(saved);
    form.append(savedLabel);
    const storageLabel = el(
      "label",
      text(
        "增量备份（复用未变化的加密内容）",
        "Incremental backups (reuse unchanged encrypted content)",
      ),
    );
    const incremental = el("input");
    incremental.type = "checkbox";
    incremental.name = "storage_mode";
    incremental.checked = current.storage_mode !== "complete";
    storageLabel.append(incremental);
    const verifyLabel = el(
      "label",
      text(
        "完整校验间隔（天，备份时检查）",
        "Full verification interval (days; checked when backing up)",
      ),
    );
    const verifyDays = el("input");
    verifyDays.type = "number";
    verifyDays.name = "full_verify_days";
    verifyDays.min = "1";
    verifyDays.max = "366";
    verifyDays.value = String(current.full_verify_days || 7);
    verifyLabel.append(verifyDays);
    const storage = el("fieldset");
    storage.append(el("legend", text("备份方式", "Backup storage")));
    storage.append(
      storageLabel,
      verifyLabel,
      el(
        "p",
        text(
          "增量模式首次完整建立，随后只保存变化文件。恢复时请保留整个备份仓库。",
          "Incremental mode stores all files first, then saves changed files. Keep the whole repository for recovery.",
        ),
        "muted",
      ),
    );
    form.append(storage);
    const policies = el("fieldset");
    policies.append(
      el(
        "legend",
        text(
          "自动备份策略（可组合）",
          "Automatic backup policies (combine as needed)",
        ),
      ),
    );
    const fields = {};
    const add = (key, zh, en, type) => {
      fields[key] = field(policies, key, zh, en, type);
    };
    add("enabled", "启用自动备份", "Enable automatic backups", "checkbox");
    add(
      "interval_minutes",
      "固定间隔（分钟，0 关闭）",
      "Interval (minutes; 0 disables)",
    );
    add(
      "daily_time",
      "每日时间（本机时区，留空关闭）",
      "Daily time (local timezone; empty disables)",
      "time",
    );
    add(
      "on_change",
      "业务数据变化后备份",
      "Back up after business data changes",
      "checkbox",
    );
    add(
      "debounce_seconds",
      "变化合并等待（秒，至少 30）",
      "Combine changes for (seconds; minimum 30)",
    );
    add(
      "growth_mb",
      "累计新增数据（MB，0 关闭）",
      "Accumulated new data (MB; 0 disables)",
    );
    add(
      "after_moc",
      "整批 MOC 挂载完成后备份",
      "Back up after a completed MOC batch",
      "checkbox",
    );
    form.append(policies);
    const advanced = el("details");
    advanced.append(
      el(
        "summary",
        text("保留策略和容量限制", "Retention and capacity limits"),
      ),
    );
    for (const [key, zh, en] of [
      ["keep_recent", "最近自动备份数", "Recent automatic backups"],
      ["keep_days", "每日版本保留天数", "Daily versions (days)"],
      ["keep_weeks", "每周版本保留周数", "Weekly versions (weeks)"],
      [
        "max_total_mb",
        "仓库容量上限（MB，0 不限制）",
        "Repository limit (MB; 0 unlimited)",
      ],
      ["max_snapshot_mb", "单次内容上限（MB）", "Snapshot content limit (MB)"],
      ["max_file_mb", "单文件上限（MB）", "File limit (MB)"],
      ["max_entries", "条目上限", "Entry limit"],
    ])
      fields[key] = field(advanced, key, zh, en);
    form.append(
      advanced,
      el(
        "p",
        text(
          "手动备份默认固定保留。清理仅处理本应用拥有的版本；最后有效备份不会删除。",
          "Manual backups are pinned by default. Cleanup touches only this app’s versions and keeps the last valid backup.",
        ),
      ),
    );
    const collect = () => {
      const policy = { ...current.policy, directory: directory.value.trim() };
      for (const [name, input] of Object.entries(fields))
        policy[name] =
          input.type === "checkbox"
            ? input.checked
            : input.type === "number"
              ? Number(input.value)
              : input.value;
      return policy;
    };
    readDraft = () => {
      const policy = collect();
      return incremental.checked !== (current.storage_mode !== "complete") ||
        Number(verifyDays.value) !== (current.full_verify_days || 7) ||
        JSON.stringify(policy) !== JSON.stringify(current.policy) ||
        key.value ||
        saved.checked
        ? {
            policy,
            key: key.value,
            saved: saved.checked,
            incremental: incremental.checked,
            verifyDays: verifyDays.value,
          }
        : null;
    };
    if (draft) {
      incremental.checked = draft.incremental;
      verifyDays.value = draft.verifyDays;
      directory.value = draft.policy.directory;
      key.value = draft.key;
      saved.checked = draft.saved;
      for (const [name, input] of Object.entries(fields)) {
        if (input.type === "checkbox") input.checked = draft.policy[name];
        else input.value = draft.policy[name];
      }
    }
    const save = el("button", text("保存备份设置", "Save backup settings"));
    save.type = "submit";
    form.append(save);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      try {
        const policy = { ...current.policy, directory: directory.value.trim() };
        for (const [name, input] of Object.entries(fields))
          policy[name] =
            input.type === "checkbox"
              ? input.checked
              : input.type === "number"
                ? Number(input.value)
                : input.value;
        await api.saveBackupAutomation({
          revision: current.revision,
          storage_mode: incremental.checked ? "incremental" : "complete",
          full_verify_days: Number(verifyDays.value),
          policy,
          recovery_key: key.value.trim(),
          key_saved: saved.checked,
        });
        key.value = "";
        readDraft = null;
        await refresh();
        message(text("备份设置已保存。", "Backup settings saved."));
      } catch (error) {
        message(errorText(error.code) || error.message, true);
      } finally {
        save.disabled = false;
      }
    });
    root.append(form);
    const actions = el("div", null, "panel-actions");
    actions.append(
      control(
        text("立即加密备份", "Back up now"),
        async (notify) => {
          await operation(() => api.runBackup());
          await refresh();
          notify(
            text(
              "已排队，请刷新查看进度。",
              "Queued. Refresh to check progress.",
            ),
          );
        },
        { localize: false },
      ),
    );
    actions.append(
      control(
        text("清理可删除版本", "Clean eligible versions"),
        async () => {
          await operation(() => api.cleanupBackups());
          await refresh();
        },
        { localize: false },
      ),
    );
    actions.append(
      control(text("刷新备份状态", "Refresh backup status"), refresh, {
        localize: false,
      }),
    );
    root.append(actions);
    statusRoot = el("div");
    statusRoot.setAttribute("aria-live", "polite");
    root.append(statusRoot);
    renderStatus(current, statusRoot);
    polling.start(current);
  }
  return {
    refresh,
    clear() {
      epoch++;
      polling.stop();
      statusRoot = null;
      readDraft = null;
      root.replaceChildren();
    },
  };
}
