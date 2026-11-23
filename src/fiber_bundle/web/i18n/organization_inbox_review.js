// GUI copy is separate from source text and file evidence.
export default {
  "inbox_review.inbox_review_restore_chain_invalid": [
    "恢复记录之间的关联无法安全核验，可能存在不一致、循环或层级过多；恢复已停止，现有文件保留。",
    "The links between restore records cannot be safely verified because they may be inconsistent, cyclic, or too deep. Restoration stopped; current files are preserved.",
  ],
  "inbox_review.inbox_review_backup_changed": [
    "修改前备份或隔离证据已变化，不能确认安全恢复，现有文件保留。",
    "The original backup or quarantine evidence changed. Safe restoration cannot be verified; current files are preserved.",
  ],
  "inbox_review.inbox_review_diff_limit": [
    "所选修改的差异超过展示上限，未生成可确认计划；请减少所选链接后重新预览。",
    "The selected changes exceed the diff display limit. No confirmable plan was created. Select fewer links and preview again.",
  ],
  "inbox_review.inbox_review_file_limit": [
    "此文件超过当前检查大小上限，未作为空文件或链接修复项处理。",
    "The file exceeds the current inspection size limit and was not processed as an empty file or a link repair.",
  ],
  "inbox_review.inbox_review_invalid_action": [
    "当前处理方式不受支持；请重新检查文件并选择界面提供的处理。",
    "This operation is unsupported. Recheck the file and choose an operation offered by the interface.",
  ],
  "inbox_review.inbox_review_move_conflict": [
    "移动目标或原路径存在冲突，处理已停止且没有覆盖同名文件。",
    "The move destination or original path conflicts with another file. Processing stopped without overwriting it.",
  ],
  "inbox_review.inbox_review_publication_quarantined": [
    "此导入的正文已由独立处理隔离，原导入不会继续发布；附件与导入历史保留。",
    "This import’s note was quarantined by a separate operation. The original import will not continue publication; attachments and import history are preserved.",
  ],
  "inbox_review.inbox_review_stage_conflict": [
    "处理阶段与已保存证据冲突，不能继续原确认。",
    "The operation stage conflicts with saved evidence. The original confirmation cannot proceed.",
  ],
  "inbox_review.inbox_review_stage_invalid": [
    "处理阶段或隔离记录无法核验，不能继续文件改写。",
    "The operation stage or quarantine record cannot be verified. File rewriting cannot proceed.",
  ],
  "inbox_review.next_move_conflict": [
    "请保留现有文件并读取处理历史，核对原路径与隔离记录；不要覆盖任何同名文件。",
    "Preserve current files and read operation history. Check the original path and quarantine record without overwriting any same-name files.",
  ],
  "inbox_review.history_truncated": [
    "这里只显示最近的部分处理记录；更早记录仍保留。",
    "Only some recent operation records are displayed here. Earlier records are preserved.",
  ],
  "inbox_review.action": ["处理：{p0}", "Operation: {p0}"],
  "inbox_review.open_index": ["查看此知识库索引", "View this vault’s index"],
  "inbox_review.inbox_review_publication_changed": [
    "关联导入记录在预览后发生变化，处理已停止。",
    "The associated import record changed after preview. Processing stopped.",
  ],
  "inbox_review.inbox_review_registration_changed": [
    "笔记登记或索引依据在预览后发生变化，处理已停止。",
    "The note registration or index evidence changed after preview. Processing stopped.",
  ],
  "inbox_review.inbox_review_already_restored": [
    "此处理已经恢复，不能再次恢复同一记录。",
    "This operation has already been restored and cannot be restored again.",
  ],
  "inbox_review.inbox_review_concurrent_edit_restore_required": [
    "处理期间检测到新增或修改的正文，需要先核对并恢复保存的文件，不能继续原处理计划。",
    "New or edited content was detected during processing. Verify and restore the saved file before proceeding; the original operation plan cannot continue.",
  ],
  "inbox_review.next_restore_preview": [
    "请读取处理历史或点击“预览恢复此处理”，核对保存的文件与原路径后确认恢复；当前正文会保留。",
    "Read operation history or choose “Preview restoring this operation,” then verify the saved file and original path before confirming restoration. Current content is preserved.",
  ],
  "inbox_review.inbox_review_restore_path_occupied": [
    "原路径已被另一个文件占用，恢复已停止且没有覆盖。",
    "The original path is occupied by another file. Restoration stopped without overwriting it.",
  ],
  "inbox_review.inbox_review_restore_file_changed": [
    "待恢复文件在处理后已变化，恢复已停止，现有修改保留。",
    "The file changed after processing. Restoration stopped and current edits are preserved.",
  ],
  "inbox_review.inbox_review_plan_changed": [
    "处理计划与当前文件证据不一致，尚未执行此计划。",
    "The operation plan no longer matches current file evidence. This plan has not been applied.",
  ],
  "inbox_review.inbox_review_stage_changed": [
    "此处理的阶段已变化，旧确认已停止。",
    "This operation has moved to a different stage. The old confirmation was stopped.",
  ],
  "inbox_review.inbox_review_concurrent_edit_preserved": [
    "检测到同时发生的编辑，已保留现有修改并停止处理。",
    "A concurrent edit was detected. Current edits are preserved and processing stopped.",
  ],
  "inbox_review.next_occupied": [
    "请先核对原路径现有文件并自行决定如何保留双方，再读取处理历史生成新的恢复预览。",
    "Review the file at the original path and decide how to preserve both files, then read history and generate a new restore preview.",
  ],
  "inbox_review.next_preserve_edit": [
    "请保留当前文件，核对后续编辑与处理历史；不要重复确认旧恢复计划。",
    "Preserve the current file and review subsequent edits and operation history. Do not repeat confirmation of the old restore plan.",
  ],
  "inbox_review.next_history": [
    "请读取处理历史并核对原操作的当前阶段，再预览恢复或核对收尾；不要重复执行文件移动或改写。",
    "Read operation history and verify the original operation’s current stage, then preview restoration or finalization. Do not repeat file moves or rewrites.",
  ],
  "inbox_review.technical": ["技术详情", "Technical details"],
  "inbox_review.timeout": [
    "未在等待时限内收到后台结果，当前结果尚未确认。",
    "No backend result arrived within the waiting limit. The outcome is not yet confirmed.",
  ],
  "inbox_review.network": [
    "与本机后台的连接中断，当前结果尚未确认。",
    "The connection to the local backend was interrupted. The outcome is not yet confirmed.",
  ],
  "inbox_review.inbox_review_file_changed": [
    "文件在检查后已变化；请重新检查并预览，当前内容已保留。",
    "The file changed after inspection. Recheck and preview it again; current content is preserved.",
  ],
  "inbox_review.inbox_review_link_selection_changed": [
    "所选链接与当前原文不再一致；请重新检查并逐项选择。",
    "The selected links no longer match the current source. Recheck and select them again.",
  ],
  "inbox_review.inbox_review_not_empty": [
    "当前文件含有正文、链接、图片或代码，不能按空文件隔离。",
    "The current file contains text, links, images, or code and cannot be quarantined as empty.",
  ],
  "inbox_review.inbox_review_unreadable": [
    "当前文件读取失败，无法确认是否为空；请核对本机文件与同步状态。",
    "The file could not be read, so emptiness is unknown. Check the local file and sync state.",
  ],
  "inbox_review.inbox_review_scope_or_directory_changed": [
    "文件目录或访问范围已变化；请重新核对权限、文件及原路径。",
    "The directory or access scope changed. Recheck permissions, the file, and its original path.",
  ],
  "inbox_review.inbox_review_reconciliation_required": [
    "此文件已有待核对处理；请读取处理历史，核对原操作后收尾。",
    "This file has an operation requiring verification. Read its history and verify the original operation before finalizing.",
  ],
  "inbox_review.inbox_review_restore_not_ready": [
    "此处理当前不能恢复；请读取处理历史并核对文件及原路径。",
    "This operation cannot currently be restored. Read its history and check the file and original path.",
  ],
  "inbox_review.inbox_review_read_only": [
    "当前授权仅允许读取，不能处理此文件。",
    "Current permissions allow reading only; this file cannot be changed.",
  ],
  "inbox_review.inbox_review_package_not_supported": [
    "专家包与 MOC 文件不在本次单篇处理范围。",
    "Expert packages and MOC files are outside this single-file workflow.",
  ],
  "inbox_review.inbox_review_publication_not_committed": [
    "此文件关联未完成导入；请保留导入记录，通过当前空文件隔离预览核对处理范围。",
    "This file belongs to an unfinished import. Preserve the import record and review the current empty-file quarantine preview.",
  ],
  "inbox_review.committing": ["发布尚未完成", "Publication incomplete"],
  "inbox_review.published": ["发布已完成", "Publication complete"],
  "inbox_review.confirm_pending": [
    "正在等待后台核对本次确认；尚未收到执行结果，请勿重复提交。",
    "Waiting for the backend to verify this confirmation. No execution result has been received; do not submit again.",
  ],
  "inbox_review.operation_pending": [
    "正在等待后台处理本次任务操作，请勿重复提交。",
    "Waiting for the backend to process this task operation. Do not submit again.",
  ],
  "inbox_review.preview_failure": [
    "预览核对未通过，尚未执行整理。请检查具体文件或目录变化，再按当前资料重新生成预览。",
    "Preview verification failed; organization has not started. Check the reported file or directory changes, then generate a preview from current files.",
  ],
  "inbox_review.open": ["检查与处理", "Inspect and resolve"],
  "inbox_review.history": [
    "Inbox 处理历史与恢复",
    "Inbox processing history and restore",
  ],
  "inbox_review.current_file": ["当前文件：{p0}", "Current file: {p0}"],
  "inbox_review.bytes": ["当前大小：{p0} 字节", "Current size: {p0} bytes"],
  "inbox_review.classification": ["正文状态：{p0}", "Content status: {p0}"],
  "inbox_review.empty": ["空文件或仅空白", "Empty or whitespace only"],
  "inbox_review.title_only": [
    "仅一个纯文本标题",
    "One plain-text heading only",
  ],
  "inbox_review.content": [
    "有正文、链接、图片或代码",
    "Contains body text, links, images, or code",
  ],
  "inbox_review.unreadable": [
    "当前不可读，不能判断是否为空",
    "Currently unreadable; emptiness cannot be determined",
  ],
  "inbox_review.unknown": ["未记录", "Not recorded"],
  "inbox_review.publication": [
    "导入记录 {p0} · 状态 {p1}",
    "Import record {p0} · State {p1}",
  ],
  "inbox_review.issue": ["当前问题：{p0}", "Current issue: {p0}"],
  "inbox_review.current_text": [
    "当前文件原文（纯文本，带行号）",
    "Current source text with line numbers",
  ],
  "inbox_review.truncated": [
    "原文展示已截断，最多读取 {p0} 字节；操作仍由后台校验完整文件。",
    "Source display is truncated to {p0} bytes. Operations still require full-file verification by the backend.",
  ],
  "inbox_review.no_text": [
    "本次读取没有可显示的文本行。",
    "This read contains no displayable text lines.",
  ],
  "inbox_review.choose_links": [
    "逐项选择已确认缺失的链接，转为普通文本并保留原文字。",
    "Select confirmed missing links individually to convert them to plain text while preserving their text.",
  ],
  "inbox_review.link": [
    "第 {p0} 行 · 缺失链接：{p1}",
    "Line {p0} · Missing link: {p1}",
  ],
  "inbox_review.line": ["第 {p0} 行", "Line {p0}"],
  "inbox_review.before": ["修改前（原文）", "Before (source)"],
  "inbox_review.after": ["修改后（原文）", "After (source)"],
  "inbox_review.plan": ["待确认：{p0}", "Review before confirmation: {p0}"],
  "inbox_review.operation": ["处理记录：{p0}", "Operation: {p0}"],
  "inbox_review.state": ["处理状态：{p0}", "Operation state: {p0}"],
  "inbox_review.prepared": [
    "预览已生成，尚未执行",
    "Preview prepared; not applied",
  ],
  "inbox_review.applying": [
    "后台已开始，结果尚待核对",
    "Backend processing started; result needs verification",
  ],
  "inbox_review.reconcile": [
    "处理结果待核对",
    "Operation result needs verification",
  ],
  "inbox_review.completed": ["本项处理已完成", "This operation is complete"],
  "inbox_review.quarantine_empty": [
    "可恢复隔离此空文件",
    "Move this empty file to recoverable quarantine",
  ],
  "inbox_review.unlink_missing": [
    "将所选缺失链接转为普通文本",
    "Convert selected missing links to plain text",
  ],
  "inbox_review.restore_quarantine": [
    "恢复隔离文件",
    "Restore quarantined file",
  ],
  "inbox_review.restore_links": [
    "恢复修改前原文",
    "Restore source text before the change",
  ],
  "inbox_review.quarantine_impact": [
    "只移动这一篇空文件到可恢复隔离区。附件、来源对象、其他版本和历史保留；不会恢复导入任务。",
    "Only this empty file moves to recoverable quarantine. Attachments, sources, other versions, and history are preserved. Import tasks are not resumed.",
  ],
  "inbox_review.unlink_impact": [
    "只将所选缺失链接转为普通文本并保留原文字；其余正文保持。修改前原文与处理记录会保留。",
    "Only selected missing links are converted to plain text with their text preserved. Other source text is unchanged. The original source and operation history are retained.",
  ],
  "inbox_review.restore_impact": [
    "后台将重新核对文件和原路径；发现修改或占用会拒绝覆盖。恢复文件不会恢复导入任务。",
    "The backend rechecks the file and original path. Changes or an occupied path block overwrites. Restoring a file does not resume its import.",
  ],
  "inbox_review.consent": [
    "我已核对本项影响与前后差异，确认执行此处理。",
    "I reviewed this operation’s impact and changes and confirm applying it.",
  ],
  "inbox_review.confirm_reconcile": [
    "核对并完成此处理",
    "Verify and finalize this operation",
  ],
  "inbox_review.confirm_apply": [
    "确认执行此处理",
    "Confirm and apply this operation",
  ],
  "inbox_review.completed_next": [
    "文件处理已完成；请按当前资料重新生成快速预览，核对分类和 MOC 后再确认整理。索引状态请在知识库索引面板核对。",
    "File processing is complete. Generate a fresh quick preview, review classifications and MOCs, then confirm organization. Check index status in the vault index panel.",
  ],
  "inbox_review.repreview": [
    "按当前资料生成快速预览",
    "Generate quick preview from current files",
  ],
  "inbox_review.generating": [
    "后台正在生成新的快速预览；完成后仍需核对并确认整理。",
    "The backend is generating a new quick preview. Review and confirmation are still required.",
  ],
  "inbox_review.checking_operation": [
    "后台正在核对原处理证据并完成收尾，请等待本次结果。",
    "The backend is verifying the original operation evidence and finalizing it. Wait for this result.",
  ],
  "inbox_review.applying_operation": [
    "后台正在执行已确认的文件处理，请等待本次结果。",
    "The backend is applying the confirmed file operation. Wait for this result.",
  ],
  "inbox_review.reading": [
    "正在读取当前文件、导入状态与可处理问题…",
    "Reading the current file, import status, and actionable issues…",
  ],
  "inbox_review.previewing": [
    "后台正在校验当前文件并生成处理预览；尚未改写文件。",
    "The backend is verifying the current file and preparing an operation preview. The file has not been changed.",
  ],
  "inbox_review.refresh_file": ["重新检查当前文件", "Recheck current file"],
  "inbox_review.stale": [
    "资料已处理，以上检查已过期；请重新检查当前文件后再处理。",
    "A file operation made this inspection stale. Recheck the current file before another operation.",
  ],
  "inbox_review.preview_quarantine": [
    "预览可恢复隔离此空文件",
    "Preview recoverable quarantine for this empty file",
  ],
  "inbox_review.preview_unlink": [
    "预览所选链接转为普通文本",
    "Preview converting selected links to plain text",
  ],
  "inbox_review.reading_history": [
    "正在读取当前授权范围的处理历史…",
    "Reading operation history for the current authorized scope…",
  ],
  "inbox_review.history_intro": [
    "处理历史保留原文件位置和操作证据。恢复也需先预览并确认；不会自动恢复旧整理或导入任务。",
    "History retains original file locations and operation evidence. Restoration also requires a preview and confirmation. Old organization or import tasks are not resumed automatically.",
  ],
  "inbox_review.refresh_history": ["读取处理历史", "Read processing history"],
  "inbox_review.no_history": [
    "当前范围尚无处理记录。",
    "No operation records exist in the current scope.",
  ],
  "inbox_review.preview_restore": [
    "预览恢复此处理",
    "Preview restoring this operation",
  ],
  "inbox_review.review_pending": [
    "核对此未完成处理",
    "Review this unfinished operation",
  ],
  "inbox_review.failed": [
    "本次操作未完成，请核对以下原因。",
    "This request did not complete. Review the reason below.",
  ],
  "inbox_review.check_again": [
    "请重新检查当前文件或读取处理历史，再按当前证据预览；不要重复确认旧计划。",
    "Recheck the current file or read operation history, then preview from current evidence. Do not repeat confirmation of an old plan.",
  ],
};
