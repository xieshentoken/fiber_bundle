// Render job status independently of editable backup settings and recovery-key inputs.
import { locale } from "./i18n/index.js";
import { el } from "./ui.js";
import { control } from "./record_ui.js";
const text = (zh, en) => (locale() === "en" ? en : zh);
const errors = {
  backup_incremental_object_missing: [
    "共享备份内容缺失。请保留整个备份仓库，不要单独移动某个版本。",
    "Shared backup content is missing. Keep the whole repository, rather than moving one snapshot.",
  ],
  backup_ciphertext_changed: [
    "备份内容已变化或损坏，本次未通过校验，旧版本已保留。",
    "Backup content changed or is damaged. Verification failed; older versions were kept.",
  ],
  backup_incremental_repository_invalid: [
    "共享备份仓库无效，请检查目录及完整性。",
    "The shared repository is invalid. Check its folder and integrity.",
  ],
  backup_policy_changed: [
    "设置已变化，请刷新后重试。",
    "Settings changed. Refresh and retry.",
  ],
  backup_job_busy: [
    "备份正在排队或执行，完成后再修改设置。",
    "A backup is pending. Edit settings after it finishes.",
  ],
  backup_target_unavailable: [
    "备份目录不可用，请挂载磁盘或重新选择目录。",
    "Backup folder unavailable. Mount the disk or choose another folder.",
  ],
  backup_capacity_limit: [
    "容量上限不足，请清理可删除版本或调整上限。",
    "Repository limit reached. Clean eligible versions or increase the limit.",
  ],
  backup_disk_full: [
    "磁盘空间不足，旧备份已保留。",
    "Disk space is insufficient. Existing backups are kept.",
  ],
  backup_total_limit: [
    "内容超过单次备份上限，请调整容量限制。",
    "Snapshot content exceeds the configured limit.",
  ],
  backup_file_limit: [
    "有文件超过单文件上限，请调整容量限制。",
    "A file exceeds the configured file limit.",
  ],
  backup_entry_limit: [
    "目录条目超过上限，请调整条目限制。",
    "The entry limit was exceeded. Increase it in capacity settings.",
  ],
  backup_source_changed: [
    "备份期间内容变化，本次未完成，请稍后重试。",
    "Content changed during backup. Retry when writes have settled.",
  ],
  backup_unclassified_state: [
    "发现尚未分类的状态目录，备份已停止，请检查版本。",
    "Unclassified state found. Backup stopped; check the application version.",
  ],
  backup_configuration_required: [
    "先选择目录并保存恢复密钥。",
    "Choose a folder and save a recovery key first.",
  ],
  backup_target_identity_changed: [
    "备份磁盘或目录已变化，请重新选择。",
    "The backup disk or folder has changed. Select it again.",
  ],
  backup_database_busy: [
    "正在等待数据库空闲；未完成备份。",
    "Waiting for the database; backup is incomplete.",
  ],
  model_keychain_unavailable: [
    "无法读取钥匙串，请解锁后重试。",
    "Unlock Keychain and retry.",
  ],
  backup_interrupted: [
    "备份已中断，原有备份保留，请重试。",
    "Backup interrupted. Previous backups are safe. Retry when ready.",
  ],
  backup_history_limit: [
    "备份记录达到上限，请先清理可删除版本。",
    "Backup history limit reached. Clean eligible versions first.",
  ],
};
export const errorText = (code) =>
  errors[code] ? text(...errors[code]) : code;
const statusLabels = {
  deleting: ["版本清理中", "Version cleanup in progress"],
  cleanup_interrupted: [
    "清理中断，请重新清理可删除版本",
    "Cleanup interrupted; run cleanup again",
  ],
  queued: ["排队/等待重试", "Queued / waiting to retry"],
  running: ["备份进行中", "Backup in progress"],
  succeeded: ["加密与内容校验通过", "Encrypted and authenticated"],
  failed: ["备份失败，可重试", "Backup failed; retry available"],
  deleted: ["已按保留策略清理", "Removed by retention policy"],
};
const reasons = {
  manual: ["手动备份", "Manual backup"],
  interval: ["间隔备份", "Interval backup"],
  daily: ["每日备份", "Daily backup"],
  business_change: ["业务内容变化", "Business changes"],
  data_growth: ["新增数据达到阈值", "Data growth threshold"],
  moc_batch: ["MOC 批次完成", "MOC batch completed"],
};
const phases = {
  capacity: ["容量预检", "Capacity preflight"],
  scanning: ["扫描与一致性检查", "Scan and consistency checks"],
  checking: ["检查中", "Checking"],
  encrypting: ["加密中", "Encrypting"],
  verifying: ["认证校验中", "Authenticating"],
  complete: ["已完成", "Complete"],
  waiting: ["等待重试", "Waiting to retry"],
  failed: ["未完成", "Incomplete"],
  interrupted: ["已中断", "Interrupted"],
  queued: ["排队中", "Queued"],
  succeeded: ["已完成", "Complete"],
};

export function createBackupJobStatus({ api, operation, refresh }) {
  let pageIndex = 0;
  return function render(current, root) {
    root.replaceChildren();
    if (current.baseline_at)
      root.append(
        el(
          "p",
          text("上次成功备份：", "Last successful backup: ") +
            new Date(current.baseline_at * 1000).toLocaleString(),
        ),
      );
    if (current.error) root.append(el("p", errorText(current.error), "error"));
    if (!current.jobs.length)
      root.append(
        el(
          "p",
          text(
            "尚无备份。请先保存目录和恢复密钥。",
            "No backups yet. Save a folder and recovery key first.",
          ),
        ),
      );
    pageIndex = Math.min(
      pageIndex,
      Math.max(0, Math.ceil(current.jobs.length / 20) - 1),
    );
    if (current.jobs.length > 20) {
      const pages = el("div", null, "panel-actions");
      const previous = control(
        text("上一页", "Previous"),
        async () => {
          pageIndex--;
          await refresh();
        },
        { localize: false },
      );
      previous.disabled = pageIndex === 0;
      const next = control(
        text("下一页", "Next"),
        async () => {
          pageIndex++;
          await refresh();
        },
        { localize: false },
      );
      next.disabled = (pageIndex + 1) * 20 >= current.jobs.length;
      pages.append(
        previous,
        el("span", `${pageIndex + 1} / ${Math.ceil(current.jobs.length / 20)}`),
        next,
      );
      root.append(pages);
    }
    for (const job of current.jobs.slice(pageIndex * 20, pageIndex * 20 + 20)) {
      const card = el("article", null, "row record");
      card.append(
        el(
          "h4",
          job.state === "succeeded" &&
            job.backup_format === "encrypted-incremental/v2"
            ? job.verification === "full"
              ? text(
                  "增量快照完成 · 全部内容已校验",
                  "Incremental snapshot complete · all content verified",
                )
              : text(
                  "增量快照完成 · 新增内容已校验",
                  "Incremental snapshot complete · new content verified",
                )
            : text(...(statusLabels[job.state] || [job.state, job.state])),
        ),
        el(
          "p",
          new Date(job.created * 1000).toLocaleString() +
            " · " +
            (reasons[job.reason]
              ? text(...reasons[job.reason])
              : job.reason || text("来源未记录", "Reason not recorded")) +
            " · " +
            (phases[job.phase || job.state]
              ? text(...phases[job.phase || job.state])
              : job.phase || job.state),
        ),
      );
      if (job.started_at) {
        const seconds = Math.max(
          0,
          Math.round((job.finished_at || Date.now() / 1000) - job.started_at),
        );
        card.append(
          el(
            "p",
            text("耗时：", "Elapsed: ") + seconds + text(" 秒", " seconds"),
          ),
        );
      }
      if (job.preflight)
        card.append(
          el(
            "p",
            text("预检内容：", "Preflight scope: ") +
              job.preflight.files +
              text(" 个文件 · ", " files · ") +
              (job.preflight.bytes / 1024 ** 2).toFixed(1) +
              " MiB",
          ),
        );
      if (job.stages || job.progress) {
        for (const phase of [
          "capacity",
          "scanning",
          "encrypting",
          "verifying",
        ]) {
          const data =
            job.stages?.[phase] ||
            (job.progress?.phase === phase ? job.progress : null);
          const row = el("div", null, "backup-stage");
          row.dataset.backupStage = phase;
          let label = text(...phases[phase]) + " · ";
          if (!data) label += text("未开始", "Not started");
          else {
            label += data.complete
              ? text("已完成", "Complete")
              : job.state === "failed"
                ? text("未完成", "Incomplete")
                : text("进行中", "In progress");
            const filesTotal =
              data.files_total ??
              (phase === "scanning" ? job.preflight?.files : undefined);
            const bytesTotal =
              data.bytes_total ??
              (phase === "scanning" ? job.preflight?.bytes : undefined);
            label +=
              " · " +
              data.files_done +
              (filesTotal === undefined ? "" : "/" + filesTotal) +
              text(" 文件", " files");
            if (Number.isFinite(data.bytes_done))
              label +=
                " · " +
                (data.bytes_done / 1024 ** 2).toFixed(1) +
                (bytesTotal === undefined
                  ? ""
                  : "/" + (bytesTotal / 1024 ** 2).toFixed(1)) +
                " MiB";
            const bar = el("progress");
            bar.setAttribute("aria-label", text(...phases[phase]));
            bar.max = 100;
            if (data.complete) bar.value = 100;
            else if (bytesTotal > 0)
              bar.value = Math.min(99, (data.bytes_done / bytesTotal) * 100);
            row.append(bar);
          }
          row.append(el("p", label));
          card.append(row);
        }
      }
      if (job.failure_details)
        card.append(
          el(
            "p",
            text("变化来源：", "Changed source: ") +
              job.failure_details.section +
              " / " +
              job.failure_details.path,
          ),
        );
      if (job.destination) card.append(el("p", job.destination));
      if (job.stats)
        card.append(
          el(
            "p",
            text("新增保存 / 复用内容：", "Newly stored / reused content: ") +
              (job.stats.new_bytes / 1024 ** 2).toFixed(2) +
              " / " +
              (job.stats.reused_content_bytes / 1024 ** 2).toFixed(2) +
              " MB",
          ),
        );
      if (job.full_verified_at)
        card.append(
          el(
            "p",
            text("最近完整校验：", "Last full verification: ") +
              new Date(job.full_verified_at * 1000).toLocaleString(),
          ),
        );
      if (job.error) card.append(el("p", errorText(job.error), "error"));
      if (job.cleanup_error)
        card.append(
          el(
            "p",
            text(
              "备份成功；保留策略清理需核查，旧版本已保留。",
              "Backup succeeded; retention cleanup needs attention. Older versions are kept.",
            ),
            "error",
          ),
        );
      if (job.state === "succeeded")
        card.append(
          control(
            job.pinned
              ? text("取消固定保留", "Unpin")
              : text("固定保留", "Pin"),
            async () => {
              await operation(() => api.pinBackup(job.id, !job.pinned));
              await refresh();
            },
            { localize: false },
          ),
        );
      if (job.state === "failed")
        card.append(
          control(
            text("明确重试备份", "Retry backup"),
            async () => {
              await operation(() => api.runBackup());
              await refresh();
            },
            { localize: false },
          ),
        );
      root.append(card);
    }
  };
}
