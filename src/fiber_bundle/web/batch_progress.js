// User-visible stages derive from persisted work, never from a successful button click.
import { intakeLabel, intakePhase } from "./channel_intake_records.js";

export function failureText(code) {
  return (
    {
      channel_connection_timeout:
        "后台上次处理超时，入库已暂停；请在消息入口核对并重新启动此入口。",
      intake_storage_busy:
        "存储暂忙，后台会在取得存储锁后继续，已完成的资料不会重复保存。",
      channel_storage_failed:
        "后台存储操作失败，请检查数据目录与磁盘空间后核对任务。",
      channel_local_io_failed: "后台文件操作失败，请检查目录权限和磁盘空间。",
      channel_runtime_validation_failed:
        "后台配置校验失败，请在消息入口查看诊断并核对策略。",
      channel_internal_failed:
        "后台处理异常，请在消息入口查看诊断；已有资料保留。",
      model_unconfigured:
        "尚未选择有效处理模型，请在知识库的提取与入库策略中配置。",
      model_disabled: "所选模型已停用，请重新选择可用模型。",
      processing_cloud_content_not_allowed:
        "Pi 整理需要明确授权向所选模型发送本次资料。",
      processing_key_budget_unconfigured:
        "模型预算尚未设置，原件已保留；请在执行与费用配置预算。",
      automatic_source_processor_not_ready:
        "资料处理环境尚未就绪，原件已保留。请在资料导入核对处理设置。",
    }[code] || intakeLabel(code)
  );
}

export function batchProgress(batch) {
  const intake = batch.intake;
  if (!intake) return null;
  const jobs = intake.jobs || [],
    links = intake.links || [];
  const failures = [
    intake.reason,
    ...links.filter((link) => !link.published).map((link) => link.reason),
    ...jobs.map((job) => job.reason),
    ...(intake.source_failures || []),
    intake.worker?.error,
  ].filter((code) => code && code !== "intake_user_aborted");
  let stage;
  if (intake.cancelled) stage = "已取消后续提取和发布；原件及已入库资料保留";
  else if (jobs.length && jobs.every((job) => job.state === "complete"))
    stage = jobs.some((job) => job.missing_images)
      ? "聊天文字已保存入库，缺失图片已在笔记中标出"
      : "笔记已保存入库，可在知识库中检索";
  else if (links.length && links.every((link) => link.published))
    stage = "笔记已保存，等待索引完成";
  else if (links.some((link) => link.model_state === "waiting_budget"))
    stage = "原件已归档，Pi 整理等待模型预算";
  else if (links.some((link) => link.state === "needs_review"))
    stage = "提取失败或内容待补齐，原件已保留";
  else if (links.some((link) => link.state === "extracting")) {
    const video = links.find(
      (link) =>
        link.state === "extracting" && link.processor === "bili-note-V3",
    );
    stage = video ? `视频处理：${intakePhase(video.phase)}` : "正在提取正文";
  } else if (
    jobs.some((job) => ["needs_review", "reconcile"].includes(job.state))
  )
    stage = jobs.some((job) => job.phase === "index")
      ? "笔记已入库，索引更新失败；修复后可继续索引"
      : "资料处理失败或等待环境，已完成资料保留，可恢复";
  else if (jobs.some((job) => job.phase === "publish"))
    stage = jobs.some((job) => job.state === "reviewed")
      ? "正文已提取，等待确认保存笔记"
      : "正在校验并保存笔记";
  else if (intake.state === "stored")
    stage = !intake.automatic_save
      ? "原件已归档；笔记自动保存尚未启用"
      : links.some((link) => ["pending", undefined].includes(link.state))
        ? "原件已归档，等待提取正文"
        : "原件已归档，正在处理候选笔记";
  else
    stage =
      {
        queued: "已进入入库队列，等待归档",
        waiting: "附件待补齐，尚未归档",
        archiving: "正在归档原件；中断时需核对后继续",
        needs_review: "原件归档失败或中断，需核对后继续",
        manual: "本批次需要确认后继续入库",
        not_queued:
          batch.state === "selected"
            ? "操作已记录，尚未进入入库队列"
            : "等待选择处理方式",
      }[intake.state] || "正在核对入库状态";
  return {
    stage,
    reasons: [...new Set(failures)].map(
      (code) => intake.messages?.[code] || failureText(code),
    ),
    active:
      ["queued", "archiving"].includes(intake.state) ||
      jobs.some((job) =>
        ["queued", "claimed", "waiting"].includes(job.state),
      ) ||
      links.some((link) => ["pending", "extracting"].includes(link.state)),
    canContinue:
      batch.state === "selected" &&
      batch.store_state !== "stored" &&
      ["not_queued", "manual", "needs_review"].includes(intake.state),
  };
}

export function createBatchProgressRefresh(
  refresh,
  onError,
  { schedule = setTimeout, cancel = clearTimeout } = {},
) {
  let visible = false,
    active = false,
    timer = null,
    generation = 0;
  function stop() {
    generation++;
    if (timer !== null) cancel(timer);
    timer = null;
  }
  function arm() {
    if (!visible || !active || timer !== null) return;
    const current = generation;
    timer = schedule(async () => {
      timer = null;
      try {
        await refresh();
      } catch (error) {
        if (visible && generation === current) onError(error);
      } finally {
        if (visible && generation === current) arm();
      }
    }, 3000);
  }
  return {
    update(items) {
      active = items.some((item) => batchProgress(item)?.active);
      if (!active) stop();
      arm();
    },
    setVisible(value) {
      visible = value;
      if (!value) stop();
      else arm();
    },
    clear() {
      visible = active = false;
      stop();
    },
  };
}
