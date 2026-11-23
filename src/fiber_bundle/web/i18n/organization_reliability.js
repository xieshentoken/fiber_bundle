export default {
  "reliability.archive_history_error": [
    "历史错误：{p0}",
    "Historical error: {p0}",
  ],
  "reliability.archive_verified_interrupted": [
    "归档已核验 · 整理仍中断",
    "Archive verified · organizing remains stopped",
  ],
  "reliability.archive_verified_paused": [
    "归档已核验 · 整理已暂停",
    "Archive verified · organizing is paused",
  ],
  "reliability.archive_verified_history": [
    "原归档错误已核验并完成收尾，以下为历史诊断。整理任务保持原状态，剩余条目不会自动继续。",
    "The original archive error was verified and finalized; the diagnostic below is historical. The organizing task keeps its current state and remaining items will not continue automatically.",
  ],
  "reliability.retained_moc": ["MOC ID：{p0}", "MOC ID: {p0}"],
  "reliability.retained_evidence_size": [
    "校验证据：{p0} 字节 · 上限 {p1} 字节",
    "Verification evidence: {p0} bytes · Limit {p1} bytes",
  ],
  "reliability.retained_missing_member": [
    "缺失成员：{p0}",
    "Missing member: {p0}",
  ],
  "reliability.scope_changed": [
    "父目录、访问范围或符号链接发生变化，当前核对已停止。请核对列出的路径、变化字段和权限；不要以等待同步或直接重试写入处理。",
    "A parent directory, access scope or symbolic link changed and verification stopped. Review the listed path, changed fields and permissions; waiting for sync or retrying writes does not resolve this conflict.",
  ],
  "reliability.history_error": [
    "历史错误：{p0}；任务已结束，已完成结果保留。",
    "Historical error: {p0}. This task ended; completed results are retained.",
  ],
  "reliability.committed_file_changed": [
    "已提交的目标文件或交换备份发生变化，归档收尾已停止。请核对列出的文件、变化字段和原操作记录；不要重新创建归档或重放写入。",
    "A committed target file or exchange backup changed and archive finalization stopped. Review the listed file, changed fields and original operation; do not create another archive or replay writes.",
  ],
  "reliability.evidence_missing": [
    "原操作的已提交写入证据不完整，不能继续收尾。请保留文件并核对原操作记录，不要重新创建归档或重放写入。",
    "The original operation has incomplete committed-write evidence. Finalization stopped. Keep the files and review the original operation without creating another archive or replaying writes.",
  ],
  "reliability.registry_changed": [
    "资料登记数据库已更换，当前核对已停止。请刷新状态后重新核对原操作，不要直接重试写入。",
    "The publication registry database was replaced and verification stopped. Refresh state and review the original operation before any further writes.",
  ],
  "reliability.catalog_failed": [
    "目录读取未完成：{p0}；任务进度仍按后台记录显示。",
    "Catalog read did not finish: {p0}. Task progress still reflects backend records.",
  ],
  "reliability.preview": [
    "分析已完成，等待确认；尚未应用或归档。",
    "Analysis is complete; waiting for confirmation. No changes or archives have been applied.",
  ],
  "reliability.finalizing": [
    "已写入 · 归档收尾待核对",
    "Written · archive finalization needs review",
  ],
  "reliability.pending": [
    "原归档操作 {p0} 项尚未收尾；已挂载不代表已归档。",
    "{p0} original archive operation(s) need finalization; mounted items are not necessarily archived.",
  ],
  "reliability.finalizer": [
    "原操作已写入，归档收尾核对尚未完成。核对原操作记录后恢复收尾，不要重新创建归档。",
    "The original operation was written; archive finalization is unverified. Review the original operation record and resume finalization without creating another archive.",
  ],
  "reliability.directory": [
    "目录观察在核对期间变化，当前步骤已停止。请核对列出的目录与变化字段，再读取状态或恢复原任务。",
    "Directory observations changed during verification and the step stopped. Review the listed directory and changed fields, then read status or resume the original task.",
  ],
  "reliability.membership": [
    "共享 MOC 的当前投影与原任务依据不一致。请核对该 MOC 及原操作记录，保留已完成结果；不要重复挂载。",
    "The shared MOC projection differs from the original task baseline. Review the MOC and original operation record; keep completed results without repeating mounts.",
  ],
  "reliability.blocker": [
    "同一知识库的原任务尚未收尾。请查看阻塞任务及其写入记录，再决定恢复或保留结果结束。",
    "An earlier task in this vault is unfinished. Review the blocking task and its writes before resuming or ending it while keeping results.",
  ],
  "reliability.links": [
    "归档因链接或依赖核对暂缓。请查看条目中的文件、链接与行号；原路径和可靠挂载保留。",
    "Archiving is deferred for link or dependency review. Check each item's file, links and line numbers; original paths and reliable memberships are retained.",
  ],
  "reliability.cloud": [
    "原文尚未在本机就绪。可准备本任务等待资料，或先在 Finder 核对同步状态。",
    "Source text is not ready locally. Prepare this task's waiting files or check sync status in Finder.",
  ],
  "reliability.index_title": ["笔记索引", "Note index"],
  "reliability.index_identity": ["索引使用的身份", "Index identity"],
  "reliability.index_read": ["刷新索引状态", "Refresh index status"],
  "reliability.index_failed": [
    "索引状态读取失败；保留上次观察，请手动刷新。",
    "Index status could not be read. The last observation is retained; refresh manually.",
  ],
  "reliability.index_loading": [
    "正在读取索引状态；尚未确认当前进度。",
    "Reading index status; current progress is unconfirmed.",
  ],
  "reliability.index_count": [
    "已索引 {p0} 篇 · 待更新 {p1} · 需核对 {p2}",
    "Indexed {p0} notes · Pending {p1} · Needs review {p2}",
  ],
};
