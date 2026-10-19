// UI message catalog; parameters preserve source data.
export default {
  "publication_preview.a52a023be4": [
    "确认将已审核内容保存到以下位置？",
    "Save reviewed content to these locations?",
  ],
  "publication_preview.d661c3d96d": ["正文", "Body"],
  "publication_preview.819407386d": ["完整内容包", "Complete content package"],
  "publication_preview.2ac7de806d": ["其他文件 {p0} 项", "{p0} other files"],
  "publication_preview.9c3f413126": [
    "其余文件保存在同一已确认布局中。",
    "Remaining files stay in the same confirmed layout.",
  ],
  "publication_preview.81d3a5cb93": [
    "确认发布位置",
    "Confirm publication locations",
  ],
  "publication_preview.05e3206020": [
    "保存到这些位置",
    "Save to these locations",
  ],
  "vault_catalog.789e00f140": [
    "已有子文件夹（只读发现）。接入完成后，可在“管理 → 保存规则”中指定目录用途。",
    "Existing subfolders (read-only discovery). After attachment, assign directory roles in Manage → Save rules.",
  ],
  "vault_catalog.401c15191f": ["重新读取子文件夹", "Reload subfolders"],
  "vault_catalog.3a48ea34f3": [
    "Web 请填写本机绝对目录路径。",
    "In the browser, enter an absolute local directory path.",
  ],
  "vault_catalog.48c1369358": [
    "Web 请填写目标绝对路径；最后一级目录必须尚不存在。",
    "In the browser, enter an absolute target path. The final directory must not already exist.",
  ],
  "vault_catalog.22000feb99": ["接入预览", "Attachment preview"],
  "vault_catalog.01e98bf6e7": [
    "笔记 {p0} 篇 · 附件 {p1} 个 · {p2} 字节",
    "{p0} notes · {p1} attachments · {p2} bytes",
  ],
  "vault_catalog.780b5035a1": ["落点：{p0}", "Destination: {p0}"],
  "vault_catalog.8e6a5aec60": [
    "引用问题 {p0} 项；源文件保持原样，复制不读取库外资源。",
    "{p0} reference issues. Sources stay unchanged; copying does not read outside the vault.",
  ],
  "vault_catalog.93fc8afd90": [
    "查看缺失、歧义与范围问题",
    "View missing, ambiguous, and out-of-scope references",
  ],
  "vault_catalog.0ce2740294": [
    "查看实际包含与排除范围",
    "View actual included and excluded scope",
  ],
  "vault_catalog.787a40e482": [
    "接入与索引任务已启动；语义分析需另外执行。",
    "Attachment and indexing started. Semantic analysis runs separately.",
  ],
  "vault_catalog.33439d2631": ["启动中", "Starting"],
  "vault_catalog.840589d495": ["接入仓库", "Attach vault"],
  "vault_catalog.62f607753e": ["复制导入", "Import a copy"],
  "vault_catalog.38a4489386": ["新建仓库", "Create library"],
  "vault_catalog.ea719eca40": [
    "任务未完成。已创建的部分目录保留，请核对后重新预检；不会自动重试。",
    "Task incomplete. Partially created directories retained. Check and preflight again; no automatic retry.",
  ],
  "vault_catalog.199474c774": ["尚无接入任务。", "No attachment tasks."],
  "vault_catalog_errors.1ff5660ff2": [
    "目录移动已中断或结果待核对，请检查原目录与系统废纸篓，再使用‘核对/恢复’；不要重复删除。",
    "Directory move interrupted or result needs checking. Inspect the original and system Trash before Check / Recover. Do not delete again.",
  ],
  "vault_catalog_errors.7cc0540064": [
    "所选知识库为只读，不能授予入库权限；请选择可写库，或取消‘允许入库’。",
    "Selected vault is read-only. Choose a writable vault or disable intake permission.",
  ],
  "vault_catalog_errors.7eedfe40d4": [
    "建立索引缺少有效查询身份，请在‘索引与查询身份’选择有查询权限的身份。",
    "Indexing needs a valid query identity. Choose an authorized identity in Index & Query Identity.",
  ],
  "vault_catalog_errors.88215b6da6": [
    "目录包含尚未下载到本机的云文件，请先在Finder中下载，或在‘排除范围’排除后重新预检。",
    "Directory contains cloud files not downloaded locally. Download them in Finder, or exclude them before preflight.",
  ],
  "vault_catalog_errors.f5800052a8": [
    "云文件处于同步中、过期或冲突状态，请在Finder解决同步状态后重新预检。",
    "Cloud files are syncing, stale, or conflicted. Resolve their state in Finder before preflight.",
  ],
  "vault_catalog_errors.f37401f408": [
    "后台未加载云文件检查辅助程序，请用应用自带启动器重启后台；无需调整知识库权限。",
    "Cloud-file helper not loaded. Restart the backend with the bundled launcher; no vault permission changes required.",
  ],
  "vault_catalog_errors.3a90e24e02": [
    "云文件检查辅助程序校验失败，请恢复完整应用包后重试。",
    "Cloud-file helper verification failed. Restore the complete app package and retry.",
  ],
  "vault_catalog_errors.f2d59ce23d": [
    "预检发现符号链接，请在‘排除范围’排除该项，或使用独立本机目录。",
    "Preflight found a symbolic link. Exclude it or use a separate local directory.",
  ],
  "vault_catalog_errors.05042655bb": [
    "预检发现单个文件超过64MiB。请在‘排除范围’排除此文件，或拆分文件后重新预检；整库累计大小没有上限。",
    "A file exceeds 64 MiB. Exclude or split it before preflight. There is no total vault size limit.",
  ],
  "vault_catalog_errors.74158db4d1": [
    "预检发现硬链接文件，无法确认独立来源。请在‘排除范围’排除此文件，或复制为独立普通文件后预检。",
    "Preflight found a hard link; independent provenance cannot be confirmed. Exclude it or copy it to a separate regular file before preflight.",
  ],
  "vault_catalog_errors.fa09ce3b90": [
    "预检发现超过单文件限制或非独立文件，请核对文件大小与硬链接，并调整‘排除范围’。",
    "Preflight found an oversized or non-independent file. Check size and hard links, then adjust exclusions.",
  ],
  "vault_catalog_errors.7a26600267": [
    "单篇Markdown笔记超过解析上限，请拆分笔记或在‘排除范围’排除该文件后重新预检。",
    "A Markdown note exceeds the parser limit. Split it or exclude it before preflight.",
  ],
  "vault_catalog_errors.2a2880d2b0": [
    "范围内的本地引用数量超过处理上限，请缩小‘包含范围’后重新预检。",
    "Local references exceed the processing limit. Narrow the included scope before preflight.",
  ],
  "vault_catalog_errors.fbc8e60eed": [
    "笔记含无法解析的本地引用，请修正引用或调整‘排除范围’。",
    "A note contains unreadable local references. Correct them or adjust exclusions.",
  ],
  "vault_catalog_errors.515b8500e0": [
    "预检期间来源文件已变化，请等待同步/编辑完成后重新预检。",
    "Sources changed during preflight. Wait for sync / editing to finish, then preflight again.",
  ],
  "vault_catalog_errors.f78fcc27cc": [
    "目录身份已变化，请核对‘源目录’，重新预检；不要重用旧删除确认。",
    "Directory identity changed. Check the source directory and preflight again. Do not reuse old deletion confirmation.",
  ],
  "vault_catalog_errors.9f61e30e08": [
    "文件或范围与预览不一致，请重新预检再确认。",
    "Files or scope differ from preview. Preflight again before confirming.",
  ],
  "vault_catalog_errors.c001c3e932": [
    "预览已过期，请重新预检。",
    "Preview expired. Preflight again.",
  ],
  "vault_catalog_errors.5b90f669fd": [
    "后台未加载本机废纸篓辅助程序，请使用应用自带启动器重启后台后重新预览；无需调整知识库权限。",
    "Trash helper not loaded. Restart the backend with the bundled launcher, then preview again. No vault permission changes required.",
  ],
  "vault_catalog_errors.209b696332": [
    "废纸篓辅助程序校验失败，请恢复或重新安装完整应用包；当前目录未移动。",
    "Trash helper verification failed. Restore or reinstall the complete app package. Directory not moved.",
  ],
  "vault_catalog_errors.15e1042845": [
    "移到系统废纸篓仅支持macOS，请在当前系统手动管理目录。",
    "System Trash requires macOS. Manage directories manually on this system.",
  ],
  "vault_catalog_errors.f46622f895": [
    "此目录受保护或只接入了部分范围，不能整体移到废纸篓；请核对‘包含/排除范围’，按需仅解除接入。",
    "Directory protected or only partly attached. It cannot be moved entirely to Trash. Check include / exclude scope; detach only if appropriate.",
  ],
  "vault_catalog_errors.9d9601850c": [
    "无法核实废纸篓操作结果，请先检查原目录和系统废纸篓，不要重复删除。",
    "Cannot verify Trash result. Check original directory and system Trash first. Do not delete again.",
  ],
  "vault_catalog_errors.fb82fc3608": [
    "废纸篓移动结果未知，请先核对原目录和系统废纸篓，再使用恢复核对。",
    "Trash move result unknown. Check original directory and system Trash before recovery checking.",
  ],
  "vault_catalog_errors.a3cefb348e": [
    "目录访问策略格式无效，请核对该目录的策略文件后重新预检。",
    "Invalid directory access policy. Check its policy file before preflight.",
  ],
  "vault_directories.8bd0282dd7": [" · 当前目录", " · Current directory"],
  "vault_directories.022068da78": [
    " · 不能作为写入目标",
    " · Cannot be a write target",
  ],
  "vault_directories.c11330b852": ["选择", "Select"],
  "vault_directories.7339e5a4e8": ["选择目录 {p0}", "Choose directory {p0}"],
  "vault_directories.be51b996f0": ["正在读取子文件夹…", "Loading subfolders…"],
  "vault_directories.61a87dfb91": ["重试读取子文件夹", "Retry subfolders"],
  "vault_directories.a4052af716": [
    "当前层没有可访问的子文件夹。",
    "No accessible subfolders at this level.",
  ],
  "vault_directories.3bbe4973f4": [
    "本层尚有目录未读取，请继续加载。",
    "More directories available at this level. Continue loading.",
  ],
  "vault_directories.20b36a8a0b": ["加载更多子文件夹", "Load more subfolders"],
  "vault_directories.2c438fe497": [
    "目录分页未能前进，请刷新后重试。",
    "Directory pagination stalled. Refresh and retry.",
  ],
  "vault_external_changes.1f6bdbd74d": [
    "发现候选位置",
    "Candidate location found",
  ],
  "vault_external_changes.992b055b23": [
    "原路径文件已替换",
    "Original-path file replaced",
  ],
  "vault_external_changes.db6e22feca": ["存在多个候选", "Multiple candidates"],
  "vault_external_changes.d8a1050b4d": [
    "未定位，需核对",
    "Not located; check required",
  ],
  "vault_external_changes.c1c2a7ae54": [
    "资料包部分正文仍在",
    "Some package body files remain",
  ],
  "vault_external_changes.38d4dc9ef5": [
    "批量核对外部改动",
    "Review external changes",
  ],
  "vault_external_changes.e924443028": [
    "扫描已登记的普通笔记、Map及已发布资料的移动、改名和替换。未定位不等于删除；默认暂不处理。只更新位置、文件身份或缺失状态，保留正文、附件、来源、历史、去重和人工 MOC 关系，不重新入库。",
    "Scan registered notes, Maps, and published sources for moves, renames, and replacements. Not located does not mean deleted; leave unchanged by default. Updates locations, file identities, or missing status only. Body, attachments, sources, history, deduplication, and manual MOCs remain; no re-import.",
  ],
  "vault_external_changes.0214485104": ["本批核对完成", "Batch check complete"],
  "vault_external_changes.d05fba9600": [
    "部分完成：已完成项不会重复执行",
    "Partly complete: completed items will not run again",
  ],
  "vault_external_changes.cbdd684e95": ["本批变更预览", "Batch change preview"],
  "vault_external_changes.9f4f3cd02a": [
    "标记缺失（保留历史）",
    "Mark missing (keep history)",
  ],
  "vault_external_changes.251ecdd3aa": [
    "将保留现有正文的人工修改。",
    "Existing manual body edits will be retained.",
  ],
  "vault_external_changes.fe88c2017c": [
    "本项未完成，请重新扫描并核对。",
    "Item incomplete. Scan and check again.",
  ],
  "vault_external_changes.0459e64ff7": [
    "不改写笔记或 Map 正文；外部移动后的旧文本链接仍需核对。完成后请更新此知识库索引。",
    "Note and Map body text unchanged. Check old text links after external moves. Update this vault's index afterward.",
  ],
  "vault_external_changes.4377274887": [
    "我已核对位置映射，并确认所选缺失项确实已删除。",
    "I checked location mappings and verified selected missing items were actually deleted.",
  ],
  "vault_external_changes.57ae234a67": [
    "确认执行本批核对",
    "Confirm batch check",
  ],
  "vault_external_changes.5406b41c67": [
    "恢复本批未完成项",
    "Resume incomplete batch items",
  ],
  "vault_external_changes.d9225f7c3e": [
    "正在逐项更新位置与缺失状态；请等待结果，未知结果时先读取批次状态。",
    "Updating locations and missing status. Wait for results; read batch status before acting on unknown results.",
  ],
  "vault_external_changes.b1654f82ac": [
    "本批进度未推进，请先读取批次状态，不要重新入库。",
    "Batch progress stalled. Read batch status first. Do not re-import.",
  ],
  "vault_external_changes.707b3d82ef": [
    "正在核对：已完成 {p0}/{p1} 项。",
    "Checking: {p0}/{p1} completed.",
  ],
  "vault_external_changes.793a9f2408": [
    "核对完成，请更新此知识库索引。",
    "Check complete. Update this vault's index.",
  ],
  "vault_external_changes.3d167e84d8": [
    "部分完成，请查看各项原因。",
    "Partly complete. See each item's reason.",
  ],
  "vault_external_changes.c7b96cc86c": [
    "重新读取本批状态",
    "Reload batch status",
  ],
  "vault_external_changes.fa6e97f30e": [
    "扫描外部改动",
    "Scan external changes",
  ],
  "vault_external_changes.02ac44b7bd": [
    "正在核对允许范围内的文件，不会修改资料或调用模型…",
    "Checking files in the allowed scope. No source edits or model calls…",
  ],
  "vault_external_changes.3179d6eafc": [
    "{p0} 项需要核对；扫描未写入笔记或分类。",
    "{p0} items need checking. Scan wrote no notes or classifications.",
  ],
  "vault_external_changes.840cb5303b": [
    "{p0} 项普通笔记文件身份变化会阻止索引；另 {p1} 项可同步路径登记。均保留原笔记 ID 和人工 MOC 关系。",
    "{p0} ordinary note identity changes block indexing; {p1} other paths can be synchronized. Original note IDs and manual MOCs remain.",
  ],
  "vault_external_changes.59a87574f4": ["处理方式 {p0}", "Action {p0}"],
  "vault_external_changes.fb1c97956d": ["暂不处理", "Leave unchanged"],
  "vault_external_changes.bcaae7e966": [
    "重新绑定当前位置",
    "Rebind current location",
  ],
  "vault_external_changes.a4572c94f5": [
    "确认已删除，标记缺失",
    "Confirm deleted; mark missing",
  ],
  "vault_external_changes.775635d66b": [
    "当前位置 {p0}",
    "Current location {p0}",
  ],
  "vault_external_changes.9e0ca94bf9": [
    "填写当前 Markdown 相对路径",
    "Enter current relative Markdown path",
  ],
  "vault_external_changes.66d006ceba": [
    "候选：{p0}（{p1}）",
    "Candidate: {p0} ({p1})",
  ],
  "vault_external_changes.d39aa6c593": ["文件身份一致", "Same file identity"],
  "vault_external_changes.e2cb91a2dc": ["正文摘要一致", "Same body digest"],
  "vault_external_changes.dfd6b12788": [
    "原路径现有文件",
    "Existing file at original path",
  ],
  "vault_external_changes.bbccbf20dc": [
    "采用所有唯一候选位置",
    "Use all unique candidates",
  ],
  "vault_external_changes.b15177c268": [
    "将所有未定位项选为已删除",
    "Select all unlocated as deleted",
  ],
  "vault_external_changes.d02375cfee": [
    "已选中未定位项；请核实确实删除后再预览确认。不会删除任何文件。",
    "Unlocated items selected. Verify actual deletion before previewing and confirming. No files will be deleted.",
  ],
  "vault_external_changes.a73c58009f": [
    "生成批量核对预览",
    "Generate batch check preview",
  ],
  "vault_external_changes.e43eed4970": [
    "请先选择需要重新绑定或确认删除的项目。",
    "Select items to rebind or confirm as deleted first.",
  ],
  "vault_external_changes.87de520689": [
    "预览待确认；尚未更新位置或缺失状态。预览有效期为10分钟。",
    "Preview awaiting confirmation. Locations and missing status unchanged. Preview expires in 10 minutes.",
  ],
  "vault_external_changes.5dfe041602": [
    "读取核对批次 · {p0}",
    "Load check batch · {p0}",
  ],
  "vault_external_changes.c1150aea01": [
    "读取核对批次 · ",
    "Load check batch ·",
  ],
  "vault_merge_api.cb26dd6384": [
    "不支持的合并操作",
    "Unsupported merge operation",
  ],
  "vault_merge_operations.43bca486c0": [
    "方案已冻结，等待完整备份与执行确认",
    "Plan frozen; full backup and execution confirmation required",
  ],
  "vault_merge_operations.041f3f31b1": [
    "暂停相关入口",
    "Pause related connections",
  ],
  "vault_merge_operations.a51d5aff12": [
    "复制并校验文件",
    "Copy and verify files",
  ],
  "vault_merge_operations.db16f79868": [
    "复制来源原件",
    "Copy original sources",
  ],
  "vault_merge_operations.d8d743d647": [
    "绑定笔记身份、来源与 MOC",
    "Bind note identities, sources, and MOCs",
  ],
  "vault_merge_operations.8f79648a5e": [
    "切换权限与入口",
    "Switch permissions and connections",
  ],
  "vault_merge_operations.a5ad5bc7ad": [
    "更新目标库索引",
    "Update target vault index",
  ],
  "vault_merge_operations.f71538ff36": [
    "合并完成，相关通道与 Agent 保持暂停",
    "Merge complete. Related channels and Agents remain paused",
  ],
  "vault_merge_operations.d7112f1064": [
    "已停止，等待核对或回退",
    "Stopped; awaiting check or rollback",
  ],
  "vault_merge_operations.ff4e3af093": ["正在回退", "Rolling back"],
  "vault_merge_operations.dd451231ab": [
    "已回退，来源及审计保留",
    "Rolled back; sources and audit retained",
  ],
  "vault_merge_operations.6364ff1851": ["方案已取消", "Plan canceled"],
  "vault_merge_operations.f273df0cc2": ["等待核对", "Awaiting check"],
  "vault_merge_operations.4ae79b8bd2": [
    "文件 {p0}/{p1}；源文件保留；完整备份{p2}。",
    "Files {p0}/{p1}; sources retained; full backup {p2}.",
  ],
  "vault_merge_operations.1936eafc0c": ["已校验", "Verified"],
  "vault_merge_operations.731e3cee49": ["尚未校验", "Not verified"],
  "vault_merge_operations.4bdc55f384": [
    "操作标识与核对信息",
    "Operation ID and check information",
  ],
  "vault_merge_operations.c21412092c": [
    "我已核对冻结的文件、目标权限和入口变更；执行此方案",
    "I checked frozen files, target permissions, and connection changes; execute this plan",
  ],
  "vault_merge_operations.bb7f07a2d5": [
    "完整加密备份目录",
    "Full encrypted backup directory",
  ],
  "vault_merge_operations.ea552f1914": [
    "备份密码（仅本次校验）",
    "Backup password (this verification only)",
  ],
  "vault_merge_operations.3afdaff73c": [
    "校验备份并开始合并",
    "Verify backup and start merge",
  ],
  "vault_merge_operations.aacfcea2d4": [
    "请先核对并勾选执行确认。",
    "Check and select execution confirmation first.",
  ],
  "vault_merge_operations.67383d32e4": [
    "先在备份页面创建包含全部源库、目标库和应用状态的完整加密备份。密码不会保存到方案。",
    "Create a full encrypted backup of source vaults, target vault, and app state on the Backup page first. Password is not saved in the plan.",
  ],
  "vault_merge_operations.5b10444e99": ["停止后续阶段", "Stop further stages"],
  "vault_merge_operations.2af118a5d0": [
    "校验现场并继续冻结方案",
    "Validate current state and resume frozen plan",
  ],
  "vault_merge_operations.fcca3f1066": [
    "回退此操作：仅删除本次创建且未修改的副本，保留源文件与审计",
    "Roll back: remove only unchanged copies created by this operation; retain sources and audit",
  ],
  "vault_merge_operations.b5000f4f30": ["校验并回退", "Validate and roll back"],
  "vault_merge_operations.2df247f0c2": [
    "请先勾选回退确认。修改过的文件会阻止回退。",
    "Select rollback confirmation first. Modified files block rollback.",
  ],
  "vault_merge_operations.e8684ce324": [
    "此库尚无合并操作。",
    "No merge operations for this vault.",
  ],
  "vault_merge_operations.5a1e3382f2": [
    "合并进度与恢复",
    "Merge progress and recovery",
  ],
  "vault_merge_operations.894b57ffd6": [
    "刷新合并进度",
    "Refresh merge progress",
  ],
  "vault_merge_view.c27b57edde": [
    "合并其他知识库到此库",
    "Merge other vaults into this vault",
  ],
  "vault_merge_view.72a7d6d860": ["执行身份", "Execution identity"],
  "vault_merge_view.e3881e3b5a": [
    "选择已改变，请重新预检。",
    "Selection changed. Preflight again.",
  ],
  "vault_merge_view.7d6325c39a": [
    "选择已具有源库和目标库查询、原件、新增权限的身份",
    "Choose an identity with query, original, and add access to source and target vaults",
  ],
  "vault_merge_view.767f5bf77d": [
    "只读预检覆盖全部受管发布、来源与 MOC。历史笔记和附件须明确选择；冻结仅保存方案，执行还需完整备份及确认。源文件保留。",
    "Read-only preflight includes all managed publications, sources, and MOCs. Explicitly select historical notes and attachments. Freezing saves only a plan. Execution needs full backup and confirmation. Sources retained.",
  ],
  "vault_merge_view.b2c7f9c26a": ["选择源库", "Select source vaults"],
  "vault_merge_view.41f493f0fd": [
    "选择历史笔记与附件",
    "Select historical notes and attachments",
  ],
  "vault_merge_view.5adc55d540": [
    "共 {p0} 个历史文件；已选 {p1} 个。受管资料始终纳入。",
    "{p0} historical files; {p1} selected. Managed sources always included.",
  ],
  "vault_merge_view.64cf700652": [
    "明确选中全部历史文件",
    "Explicitly select all historical files",
  ],
  "vault_merge_view.d8595637ca": ["清除历史选择", "Clear historical selection"],
  "vault_merge_view.7a4c28d0e9": [
    "合并后目标库权限（未选身份会暂停目标库权限）",
    "Target permissions after merge (unselected identities will be paused)",
  ],
  "vault_merge_view.8e508fb98b": ["{p0} · 查询", "{p0} · Query"],
  "vault_merge_view.40dc95acd3": [" · 查询", " · Query"],
  "vault_merge_view.c8d7aa3a37": ["原件", "Original"],
  "vault_merge_view.2ecbcce474": [
    "入口切换（相关通道会暂停，完成后由用户重新启动）",
    "Connection switching (related channels pause; restart them manually afterward)",
  ],
  "vault_merge_view.c13e5ead29": [
    "设为默认查询库",
    "Set as default query vault",
  ],
  "vault_merge_view.23685e1e12": [
    "只读预检合并方案",
    "Read-only merge preflight",
  ],
  "vault_merge_view.a47e81b3a8": [
    "预检通过，尚未执行",
    "Preflight passed; not executed",
  ],
  "vault_merge_view.d7f493d4d9": [
    "存在冲突，尚未执行",
    "Conflicts found; not executed",
  ],
  "vault_merge_view.76f889fc44": [
    "{p0} 个文件，含原件共 {p1} 字节；保留 {p2} 个未选择历史文件。",
    "{p0} files, {p1} bytes including originals; {p2} unselected historical files retained.",
  ],
  "vault_merge_view.8de072932f": ["查看路径映射", "View path mapping"],
  "vault_merge_view.70fc0f61c8": [
    "这里只展示前 200 项；完整冻结方案保留全部清单。",
    "Showing first 200 only. Full frozen plan retains the complete list.",
  ],
  "vault_merge_view.3a8ec7b9b5": [
    "核对合并后权限与入口",
    "Check merged permissions and connections",
  ],
  "vault_merge_view.5e3207fc08": [
    "{p0}：查询{p1}，原件{p2}，新增{p3}",
    "{p0}: query {p1}, originals {p2}, add {p3}",
  ],
  "vault_merge_view.ce7ef28b67": ["允许", "Allow"],
  "vault_merge_view.8d12fc0d4e": ["暂停", "Pause"],
  "vault_merge_view.b5c7fce891": [
    "将暂停 {p0} 个相关通道，切换 {p1} 个通道；相关 Agent 需重新应用配置。未列出的目标身份权限将暂停。",
    "Pause {p0} related channels and switch {p1}. Related Agents need configuration reapplied. Unlisted target identity permissions will be paused.",
  ],
  "vault_merge_view.79ce5a47ca": [
    "冻结当前完整方案（不复制）",
    "Freeze full plan (no copying)",
  ],
  "vault_merge_view.7e902ac048": [
    "方案已冻结：{p0}。请在下方核对完整备份后执行。",
    "Plan frozen: {p0}. Check full backup below before execution.",
  ],
  "vault_moc_registration.0d2dfa42cb": [
    "已有 MOC 登记接入",
    "Register existing MOCs",
  ],
  "vault_moc_registration.f81e5bf405": [
    "读取保存规则中 MOC 目录的现有文件。确认后保留人工内容，仅追加系统关联区；不会重新生成目录笔记或分类整库。",
    "Read existing files in the save-rule MOC directory. Confirmation retains manual content and appends a system-association block only. No regenerated index notes or whole-vault classification.",
  ],
  "vault_moc_registration.342050f893": [
    "正在读取已有 MOC…",
    "Loading existing MOCs…",
  ],
  "vault_moc_registration.f336e42c6f": [
    "登记待恢复：{p0}",
    "Registration recovery pending: {p0}",
  ],
  "vault_moc_registration.65f00f3191": [
    "核对并恢复 MOC 登记",
    "Check and recover MOC registration",
  ],
  "vault_moc_registration.06a86390d9": [
    "已确认原文件未改动，请再次点击恢复以准备新的安全写入。",
    "Original file confirmed unchanged. Click Recover again to prepare a new safe write.",
  ],
  "vault_moc_registration.2eff01e271": [
    "已接入 {p0} 个；待接入 {p1} 个。",
    "{p0} registered; {p1} pending.",
  ],
  "vault_moc_registration.cd3917cf22": [
    "本次最多显示 1000 个 MOC 文件。",
    "Showing up to 1000 MOC files.",
  ],
  "vault_moc_registration.55440eaee0": [
    "待接入的已有 MOC",
    "Existing MOCs awaiting registration",
  ],
  "vault_moc_registration.ec84aff509": [
    "确认登记并保留人工内容",
    "Confirm registration; keep manual content",
  ],
  "vault_moc_registration.3afe052d6c": [
    "MOC 登记未完成，请核对恢复记录。",
    "MOC registration incomplete. Check recovery records.",
  ],
  "vault_moc_registration.268d62db56": [
    "预览接入已有 MOC",
    "Preview existing MOC registration",
  ],
  "vault_moc_registration.764fbf4938": [
    "请选择现有 MOC 文件。",
    "Select an existing MOC file.",
  ],
  "vault_moc_registration.4a78ba4157": ["文件：{p0}", "File: {p0}"],
  "vault_moc_registration.0b6032003c": [
    "保留人工内容 {p0} 字节；只追加系统关联区。",
    "Retain {p0} bytes of manual content; append system-association block only.",
  ],
  "vault_moc_registration.3256623840": [
    "读取 MOC 失败：{p0}",
    "Cannot read MOC: {p0}",
  ],
  "vault_moc_registration.7f39c4fffa": ["重试读取 MOC", "Retry MOC loading"],
  "vault_placement_api.81d147ae5d": [
    "普通笔记或 Map 的文件身份已变化，请在批量核对外部改动中保留原身份重新绑定。",
    "Ordinary note or Map identity changed. Rebind while retaining the original identity in Review external changes.",
  ],
  "vault_placement_api.6d6cdbcf4e": [
    "登记的知识库目录身份已变化，请先核对知识库位置。",
    "Registered vault directory identity changed. Check the vault location first.",
  ],
  "vault_placement_api.fb6b8b761a": [
    "同一份资料重复选择，请重新扫描。",
    "Same source selected twice. Scan again.",
  ],
  "vault_placement_api.f454f680da": [
    "尚有正文或候选位置，或未确认删除；请核对位置，不会自动标记缺失。",
    "Body or candidate location remains, or deletion unconfirmed. Check locations; missing status is not set automatically.",
  ],
  "vault_placement_api.435f2ed7d6": [
    "本项未完成，请核对后台与文件状态后恢复；历史和原文件保留。",
    "Item incomplete. Check backend and files before recovery. History and originals retained.",
  ],
  "vault_placement_api.48400b164e": [
    "位置记录已变化，请重新扫描并预览。",
    "Location record changed. Scan and preview again.",
  ],
  "vault_placement_api.75a826517e": [
    "目标已属于另一份资料，不能重复绑定。",
    "Target belongs to another source. Duplicate binding rejected.",
  ],
  "vault_placement_api.32bd008ea7": [
    "目标不在当前允许范围，不能重新绑定。",
    "Target outside allowed scope. Rebinding rejected.",
  ],
  "vault_placement_api.bf7e348c6f": [
    "资料包需要保持完整目录，请填写包内 note.md 路径。",
    "Package must retain its complete directory. Enter the package's note.md path.",
  ],
  "vault_placement_api.7d9d8ece09": [
    "资料包成员发生变化，请逐项核对，不能拆包绑定。",
    "Package members changed. Check each member; partial package binding rejected.",
  ],
  "vault_placement_api.e22aa85386": [
    "请填写允许范围内的 Markdown 相对路径。",
    "Enter a relative Markdown path inside the allowed scope.",
  ],
  "vault_placement_api.0c088dc308": [
    "请先核对预览并明确确认。",
    "Check the preview and explicitly confirm first.",
  ],
  "vault_placement_api.956d280bb8": [
    "完整专家 Draft 请使用专门的整包归档入口，目标取自保存规则。",
    "Use the dedicated whole-package archive action for complete expert Drafts. Destination comes from save rules.",
  ],
  "vault_placement_api.f69ded59b3": [
    "当前选择的内容不是完整专家 Draft。",
    "Selected content is not a complete expert Draft.",
  ],
  "vault_placement_api.0590eef3f2": [
    "归档会影响包内链接，为保留原包完整性已停止，请先核对引用。",
    "Archive would affect package links. Stopped to preserve package integrity. Check references first.",
  ],
  "vault_placement_api.f43eb2c760": [
    "MOC 文件、目录或规则已变化，请重新读取并预览。",
    "MOC file, directory, or rules changed. Reload and preview again.",
  ],
  "vault_placement_api.9c06435625": [
    "该 MOC 已接入，请刷新后在关联列表选择。",
    "MOC already registered. Refresh and select it in the association list.",
  ],
  "vault_placement_api.09bcf2b915": [
    "MOC 接入预览已过期，请重新预览。",
    "MOC registration preview expired. Preview again.",
  ],
  "vault_placement_api.cc3b6b0bb2": [
    "MOC 接入尚未完成，请核对恢复记录。",
    "MOC registration incomplete. Check recovery records.",
  ],
  "vault_placement_api.5f87210ce3": [
    "该文件已有其他系统关联区，请先核对归属。",
    "File contains another system-association block. Check ownership first.",
  ],
  "vault_placement_api.e76aa58913": [
    "MOC 目录文件或改写记录已变化，请刷新并重新预览。",
    "MOC directory files or write records changed. Refresh and preview again.",
  ],
  "vault_placement_api.453393662c": [
    "正文或部分包仍存在，请使用位置重新绑定，不可标记缺失。",
    "Body or part of package remains. Rebind its location instead of marking missing.",
  ],
  "vault_placement_api.1832e5141b": [
    "发布状态已变化，请刷新后重新核对。",
    "Publication status changed. Refresh and check again.",
  ],
  "vault_placement_api.e51c2e0005": [
    "发布内容已在外部移动或替换，请先在整理面板重新绑定位置。",
    "Published content moved or replaced externally. Rebind its location in Organization first.",
  ],
  "vault_placement_api.b98f151d23": [
    "原文件仍存在，不能将另一个副本合并为同一身份。",
    "Original file still exists. Another copy cannot be merged into the same identity.",
  ],
  "vault_placement_api.6c3c068010": [
    "文件或配置已变化，请重新预览位置绑定。",
    "File or configuration changed. Preview location binding again.",
  ],
  "vault_placement_api.17849c08a9": [
    "包内资源摘要变化，请先人工核对；文件已保留。",
    "Package resource digest changed. Check manually. Files retained.",
  ],
  "vault_placement_api.ffa2018053": [
    "请先在保存规则中预览并启用标题笔记布局。",
    "Preview and enable title-note layout in Save rules first.",
  ],
  "vault_placement_api.7a83af27b1": [
    "笔记或其他链接内容已变化，请重新读取并预览。",
    "Note or linked content changed. Reload and preview again.",
  ],
  "vault_placement_api.3363e5d4b7": [
    "规则、目录或权限已变化；请保留当前文件并重新核对。",
    "Rules, directories, or permissions changed. Keep current files and check again.",
  ],
  "vault_placement_api.27cb055eec": [
    "存在未完成的操作，请先核对恢复记录。",
    "An operation is incomplete. Check recovery records first.",
  ],
  "vault_placement_api.a20f166fbc": [
    "MOC 归属或版本已变化，请重新读取并预览。",
    "MOC memberships or version changed. Reload and preview again.",
  ],
  "vault_placement_api.be1be5d7b6": [
    "请先解决目标目录选择或待修复链接。",
    "Resolve target directory selection or broken links first.",
  ],
  "vault_placement_api.0101fa302c": [
    "目标已存在同名内容，请调整归档映射后重新预览。",
    "Same-named content exists at the target. Adjust archive mapping and preview again.",
  ],
  "vault_placement_api.9087cf6720": [
    "笔记已位于所选归档目录。",
    "Note is already in the selected archive directory.",
  ],
  "vault_placement_api.5297d28ce7": [
    "预览已过期，请重新预览。",
    "Preview expired. Preview again.",
  ],
  "vault_placement_api.4b0be8c5e8": [
    "旧包已变化，请重新预览；原文件已保留。",
    "Old package changed. Preview again. Original files retained.",
  ],
  "vault_placement_api.b635a5faf3": [
    "旧包包含未登记文件，请先人工核对。",
    "Old package contains unregistered files. Check manually first.",
  ],
  "vault_placement_api.f3bc35cbd6": [
    "旧包附件已变化，请先人工核对。",
    "Old package attachments changed. Check manually first.",
  ],
  "vault_placement_api.41b59b15c8": [
    "保存规则绑定失效，请重新选择目录并确认启用。",
    "Save-rule binding expired. Select directories again and confirm activation.",
  ],
  "vault_placements.61cb89334f": [
    "切换会关闭当前预览，未确认的操作不会执行。是否继续？",
    "Switching closes this preview. Unconfirmed actions will not execute. Continue?",
  ],
  "vault_placements.66c741df1b": [
    "整理使用的客户端权限",
    "Client permissions for organization",
  ],
  "vault_placements.1fc0d5cfaf": [
    "{p0} · 整理与布局迁移",
    "{p0} · Organization & Layout Migration",
  ],
  "vault_placements.0235bd653e": [
    "MOC 关联与归档分别记录。历史转换仅处理你逐项选定的已发布内容，并保留旧包及附件。",
    "MOC associations and archiving are recorded separately. Historical conversion handles only explicitly selected publications and retains old packages and attachments.",
  ],
  "vault_placements.a3fa08099e": ["关闭整理", "Close organization"],
  "vault_placements.d48efd7446": [
    "需要可查询且可新增的客户端，以及可写的仓库。",
    "Requires a client with query and add access, and a writable vault.",
  ],
  "vault_placements.7a358d1b2f": [
    "正在读取可整理笔记与操作记录…",
    "Loading organizable notes and operation records…",
  ],
  "vault_placements.f457565caa": [
    "当前操作尚未完成，请等待结果。",
    "Current operation incomplete. Wait for the result.",
  ],
  "vault_placements.c9292212ba": [
    "MOC 自动归档任务",
    "MOC automatic archive tasks",
  ],
  "vault_placements.557e03e5c9": [
    "停止此归档（保留已确认关联）",
    "Stop this archive (keep confirmed associations)",
  ],
  "vault_placements.01152d9b4a": [
    "核对冻结版本并继续",
    "Check frozen version and continue",
  ],
  "vault_placements.274c9f0888": [
    "多个 MOC 对应不同目录，请在下方归档预览中明确选择。",
    "MOCs map to different directories. Explicitly choose a destination in the archive preview below.",
  ],
  "vault_placements.73c3e12523": [
    "待处理：{p0} · {p1}",
    "Pending: {p0} · {p1}",
  ],
  "vault_placements.899906fa47": [
    "历史布局转换",
    "Historical layout conversion",
  ],
  "vault_placements.18b97c10b6": ["位置重新绑定", "Location rebinding"],
  "vault_placements.7522e2cd3c": ["归档或关联", "Archive or association"],
  "vault_placements.b91371b3a7": ["取消待确认操作", "Cancel pending operation"],
  "vault_placements.1351e21c83": [
    "核对并恢复此操作",
    "Check and recover operation",
  ],
  "vault_placements.1539e2ff9b": ["仅关联", "Associate only"],
  "vault_placements.79f9ba5d85": ["关联状态：{p0}", "Association status: {p0}"],
  "vault_placements.106d1ff00a": [
    "待修复：{p0} → {p1}",
    "Repair needed: {p0} → {p1}",
  ],
  "vault_placements.53e0b579a0": ["待处理文件映射", "Pending file mapping"],
  "vault_placements.c599391184": [
    "确认执行以上待确认预览",
    "Confirm the pending previews above",
  ],
  "vault_placements.2142bbacc2": [
    "{p0} 项历史发布已标记文件缺失，历史与去重保留；文件找回后可在下方重新绑定。",
    "{p0} historical publications marked missing. History and deduplication retained. Rebind below if files are found.",
  ],
  "vault_placements.ec5643b41b": [
    "笔记位置或文件身份已变化，请先使用上方入口重新绑定。",
    "Note location or file identity changed. Rebind above first.",
  ],
  "vault_placements.2c54e7b555": [
    "存在登记或归档待核对操作，请先恢复。关联可能已完成，文件保留在操作记录中。",
    "Registration or archive needs checking. Recover first. Associations may already exist; files remain in operation records.",
  ],
  "vault_placements.9333dfbe16": [
    "请先在保存规则中启用标题笔记布局。",
    "Enable title-note layout in Save rules first.",
  ],
  "vault_placements.b60d65cdbf": ["待整理笔记", "Notes to organize"],
  "vault_placements.59ac9f7bd8": [
    "明确关联的 MOC",
    "Explicitly associated MOC",
  ],
  "vault_placements.304620add0": [
    "冲突时选择归档目录",
    "Archive destination on conflict",
  ],
  "vault_placements.112f5e1661": [
    "选择明确关联的 MOC",
    "Choose an explicit MOC association",
  ],
  "vault_placements.e00aa2880b": ["确认执行此预览", "Confirm this preview"],
  "vault_placements.d784615b0a": [
    "MOC 已关联，正文仍在当前位置。",
    "MOC associated. Body stays at its current location.",
  ],
  "vault_placements.a5984d43c4": [
    "归档已完成，索引等待更新。",
    "Archive complete. Index update pending.",
  ],
  "vault_placements.196dd1bcb9": [
    "没有可整理的笔记。",
    "No notes to organize.",
  ],
  "vault_placements.d67221464b": [
    "请选择明确关联的 MOC。",
    "Choose an explicit MOC association.",
  ],
  "vault_placements.a50c4fca41": ["当前位置：{p0}", "Current location: {p0}"],
  "vault_placements.ee2a1ff4b8": ["归档位置：{p0}", "Archive location: {p0}"],
  "vault_placements.755ab1c3ae": [
    "仅建立人工 MOC 关联，正文与图片位置保持。",
    "Create manual MOC association only. Body and image locations remain.",
  ],
  "vault_placements.e6462661c5": [
    "多个 MOC 的归档目录冲突，请选择后重新预览。",
    "Selected MOCs have conflicting archive directories. Choose one and preview again.",
  ],
  "vault_placements.b107072f23": [
    "完整专家 Draft：{p0} 个文件，整包移至完整专家 Draft 目录，包内字节和结构保持；知识仍未核实。",
    "Complete expert Draft: {p0} files. Move the whole package to the expert Draft directory, retaining bytes and structure. Knowledge remains unverified.",
  ],
  "vault_placements.900f3742bd": ["选择归档目录", "Choose archive directory"],
  "vault_placements.f0becac4b5": [
    "需改写的笔记或 MOC 链接：{p0}",
    "Note or MOC links to rewrite: {p0}",
  ],
  "vault_placements.06269d9caa": ["文件位置映射", "File location mapping"],
  "vault_placements.ff95808001": ["仅关联 MOC", "Associate MOC only"],
  "vault_placements.01fc952e71": ["预览归档", "Preview archive"],
  "vault_placements.f82e9df455": [
    "关联并预览归档",
    "Associate and preview archive",
  ],
  "vault_placements.d419b589df": [
    "按所选目录重新预览",
    "Preview again with selected directory",
  ],
  "vault_placements.aa7105d132": [
    "预览专家 Draft 整包归档",
    "Preview whole expert Draft archive",
  ],
  "vault_placements.353a7ae86d": [
    "关联并预览专家 Draft 整包归档",
    "Associate and preview whole expert Draft archive",
  ],
  "vault_placements.10ec58c0b6": [
    "暂无采用新布局的已发布笔记。旧目录包可在下方单独转换。",
    "No publications using the new layout. Convert old directory packages separately below.",
  ],
  "vault_placements.2baeab9593": [
    "逐项选择旧发布包",
    "Select old published packages individually",
  ],
  "vault_placements.6805613cee": [
    "可选：历史布局转换",
    "Optional: historical layout conversion",
  ],
  "vault_placements.00352ea60c": [
    "每次只转换一个明确选中的已发布包。执行前生成元数据备份，旧包与图片原件全部保留；本应用索引只展示新入口。",
    "Convert one explicitly selected published package at a time. Metadata backup is generated first; old package and original images are retained. App index shows the new entry only.",
  ],
  "vault_placements.558f13ff3b": [
    "确认备份并转换所选旧包",
    "Confirm backup and convert selected package",
  ],
  "vault_placements.e0095c0bc0": [
    "所选旧包已转换；原包与附件保留，索引需要更新。",
    "Selected package converted. Original package and attachments retained; index update required.",
  ],
  "vault_placements.79168fb70b": [
    "读取可转换的旧包",
    "Load convertible old packages",
  ],
  "vault_placements.1d8c06b81e": [
    "没有尚未转换的已发布包。",
    "No unconverted published packages.",
  ],
  "vault_placements.49a11a0a60": [
    "预览所选旧包转换",
    "Preview selected package conversion",
  ],
  "vault_placements.ffcb96cad6": [
    "请先读取并选择一个旧包。",
    "Load and select an old package first.",
  ],
  "vault_placements.585ee27dca": [
    "保留的人工修改：{p0}",
    "Retained manual edits: {p0}",
  ],
  "vault_placements.c17af852fb": ["转换文件位置", "Converted file locations"],
  "vault_placements.a62dedd709": [
    "刷新整理状态",
    "Refresh organization status",
  ],
  "vault_rebind.4d76b44a4d": [
    "外部移动或替换后重新绑定位置",
    "Rebind after external move or replacement",
  ],
  "vault_rebind.32b83a2fde": [
    "只更新文件位置和身份记录，保留来源与 MOC；不修改正文。外部移动后的链接仍需核对。",
    "Updates file locations and identity records only. Sources and MOCs retained; body unchanged. Check links after external moves.",
  ],
  "vault_rebind.f3a2c1297a": [
    "需要重新绑定的发布内容",
    "Publications requiring rebinding",
  ],
  "vault_rebind.7fb5752ef2": [
    "重新绑定的入口相对路径",
    "Relative entry path for rebinding",
  ],
  "vault_rebind.fe5fb792c3": ["[已标记缺失] ", "[Marked missing]"],
  "vault_rebind.c02f8bfa53": [
    "确认标记缺失（保留历史）",
    "Confirm missing status (keep history)",
  ],
  "vault_rebind.53a0388cfc": ["确认重新绑定", "Confirm rebinding"],
  "vault_rebind.f9c9ec5486": ["预览重新绑定", "Preview rebinding"],
  "vault_rebind.f2ef85600e": [
    "当前正文摘要：{p0}",
    "Current body digest: {p0}",
  ],
  "vault_rebind.2833fd5165": [
    "保留人工改动：{p0}",
    "Retained manual edits: {p0}",
  ],
  "vault_rebind.c2f61f0c77": ["重绑文件位置映射", "Rebound file mapping"],
  "vault_rebind.50ec7720f0": [
    "预览标记文件缺失",
    "Preview missing-file status",
  ],
  "vault_rebind.d0a81b5dfd": [
    "此记录已标记为缺失。文件找回后可重新绑定位置。",
    "Record already marked missing. Rebind when the file is found.",
  ],
  "vault_rebind.95db2fa269": [
    "将保留历史、来源和去重，仅将以下不存在的正文标记为缺失；不恢复或删除文件：",
    "Keep history, sources, and deduplication. Mark only the missing body files below; no file restoration or deletion:",
  ],
  "vault_role_fields.6f9daa4946": ["目录规则版本", "Directory rule version"],
  "vault_role_fields.c43c3b1167": ["兼容现有规则", "Compatible existing rules"],
  "vault_role_fields.3792e60504": [
    "统一知识库目录",
    "Unified vault directories",
  ],
  "vault_role_fields.f5cee96ef0": ["MOC 默认目录", "Default MOC directory"],
  "vault_role_fields.fa53bcbdcb": [
    "完整专家 Draft 目录",
    "Complete expert Draft directory",
  ],
  "vault_role_fields.84dbd7c3c7": [
    "附件根目录（不索引正文）",
    "Attachment root (body not indexed)",
  ],
  "vault_role_fields.e9538a3517": [
    "非图片原件与附件目录",
    "Non-image originals and attachments",
  ],
  "vault_role_fields.c75172dddc": [
    "确认 MOC 后归档方式",
    "Archive after MOC confirmation",
  ],
  "vault_role_fields.7e344fb8ba": ["仅准备预览", "Prepare preview only"],
  "vault_role_fields.bbf032b708": [
    "自动执行（普通笔记）",
    "Automatic execution (ordinary notes)",
  ],
  "vault_role_fields.253e5c74b0": [
    "关闭：仅建立 MOC 关联，不移动笔记。",
    "Off: associate MOCs only; keep notes in place.",
  ],
  "vault_role_fields.f13d566292": [
    "仅准备预览：生成归档预览，确认后再移动。",
    "Preview only: generate an archive preview; move after confirmation.",
  ],
  "vault_role_fields.3d9e59145a": [
    "自动执行（普通笔记）：确认关联后，自动归档符合条件的普通笔记。",
    "Automatic (ordinary notes): after association confirmation, archive eligible ordinary notes automatically.",
  ],
  "vault_role_fields.848da3c366": [
    "归档优先使用 MOC 对应目录，否则使用“已整理默认目录”；专家包沿用 Book 规则。",
    "Use the MOC's mapped directory first, otherwise the default organized directory. Expert packages follow Book rules.",
  ],
  "vault_role_fields.482ef19277": [
    "填入统一库目录预设",
    "Fill unified directory presets",
  ],
  "vault_save_api.2b1dc40d1b": [
    "图片与非图片目录须是附件根目录下互不重叠的子目录。",
    "Image and non-image directories must be non-overlapping children of the attachment root.",
  ],
  "vault_save_api.787937b8c9": [
    "保存规则已被修改。请重新打开规则，核对后再保存。",
    "Save rules changed. Reopen, check, then save.",
  ],
  "vault_save_api.906792cbc0": [
    "目录或权限已变化，请刷新目录并重新预览。",
    "Directories or permissions changed. Refresh directories and preview again.",
  ],
  "vault_save_api.5edff1894c": [
    "上次目录创建未完成，请先核对并结束待处理操作。",
    "Previous directory creation incomplete. Check and finish the pending operation first.",
  ],
  "vault_save_api.8c35feb2bd": [
    "该仓库为只读，不能保存写入规则或创建目录。",
    "Vault read-only. Write rules and directory creation unavailable.",
  ],
  "vault_save_api.15ca301082": [
    "笔记、MOC、专家包与附件的目录用途冲突，请选择互不覆盖的目录。",
    "Note, MOC, expert package, and attachment roles conflict. Choose non-overlapping directories.",
  ],
  "vault_save_api.e1902d6da3": [
    "所选目录不在允许写入的范围内，请调整选择。",
    "Directory outside allowed write scope. Adjust selection.",
  ],
  "vault_save_api.3e44647c4c": [
    "目录名称存在大小写或字符形式冲突，请从已发现目录中选择。",
    "Directory names conflict in case or character normalization. Choose from discovered directories.",
  ],
  "vault_save_api.91ccaecb8b": [
    "目录不可写，请检查本机文件权限。",
    "Directory not writable. Check local file permissions.",
  ],
  "vault_save_api.4a5838c320": [
    "MOC 已变化或不可用，请重新打开保存规则。",
    "MOC changed or unavailable. Reopen Save rules.",
  ],
  "vault_save_api.0b0c148b03": [
    "目录内容过多，请缩小配置范围。",
    "Too many directory entries. Narrow the configured scope.",
  ],
  "vault_save_api.6f6cb2c22a": [
    "目录预览已过期，请重新读取目录并预览。",
    "Directory preview expired. Reload directories and preview again.",
  ],
  "vault_save_api.12a78d7e99": [
    "仓库或目录已变化，请重新选择并刷新目录。",
    "Vault or directory changed. Reselect and refresh directories.",
  ],
  "vault_save_api.2ccb1442ba": [
    "目录列表已变化，请刷新后重新展开。",
    "Directory list changed. Refresh and expand again.",
  ],
  "vault_save_api.0db3182666": [
    "目录不在允许访问的范围内。",
    "Directory outside allowed access scope.",
  ],
  "vault_save_api.fa0c5f63a9": [
    "未完成的预览较多，请稍后重新读取目录。",
    "Too many unfinished previews. Reload directories later.",
  ],
  "vault_save_settings.dc8318fc3c": [
    "切换仓库会清除未保存的规则草稿，是否继续？",
    "Switching vaults clears unsaved rule drafts. Continue?",
  ],
  "vault_save_settings.591e57920a": [
    "正在读取保存规则…",
    "Loading save rules…",
  ],
  "vault_save_settings.2c43bb60e7": ["重试读取规则", "Retry rules"],
  "vault_save_settings.a240eec251": ["{p0} · 保存规则", "{p0} · Save rules"],
  "vault_save_settings.a3fdba54bd": ["保存形式", "Save format"],
  "vault_save_settings.138175ac4b": ["独立文件夹", "Separate folders"],
  "vault_save_settings.db83e72863": [
    "标题笔记与集中图片",
    "Title notes and shared images",
  ],
  "vault_save_settings.0b7e287692": ["保存位置", "Save locations"],
  "vault_save_settings.05af30bb8d": ["归档规则", "Archive rules"],
  "vault_save_settings.e7f468a900": ["选择目录…", "Choose directory…"],
  "vault_save_settings.f629815e11": [
    "{p0}：选择目录",
    "{p0}: choose directory",
  ],
  "vault_save_settings.1ae67e7d11": ["{p0}：相对路径", "{p0}: relative path"],
  "vault_save_settings.5e367c8502": [
    "选择已有目录，或填写待创建的相对路径",
    "Choose an existing directory or enter a relative path to create",
  ],
  "vault_save_settings.542761b200": [
    "未分类笔记目录（兼容规则包含专家 Draft）",
    "Unclassified note directory (legacy rules include expert Drafts)",
  ],
  "vault_save_settings.573e4c7b4c": [
    "已整理默认目录",
    "Default organized directory",
  ],
  "vault_save_settings.f60e8b0f65": ["共享图片目录", "Shared image directory"],
  "vault_save_settings.fbbbb120f5": ["图片引用方式", "Image link style"],
  "vault_save_settings.cb7ebacfd3": ["Obsidian 引用", "Obsidian links"],
  "vault_save_settings.6b5bab4992": [
    "Markdown 相对引用",
    "Relative Markdown links",
  ],
  "vault_save_settings.4a5f0c99aa": [
    "确认 MOC 关联后准备归档",
    "Prepare archive after MOC association confirmation",
  ],
  "vault_save_settings.683a4bf5b2": [
    "确认启用本规则用于随后新准备的发布",
    "Confirm enabling this rule for future prepared publications",
  ],
  "vault_save_settings.23701f1723": [
    "{p0}」归档目录",
    "{p0} archive directory",
  ],
  "vault_save_settings.5bf45c0bbc": ["」归档目录", " archive directory"],
  "vault_save_settings.c294db4886": [
    "留空使用已整理默认目录",
    "Leave blank to use the default organized directory",
  ],
  "vault_save_settings.b2cc2351d7": [
    "MOC 较多，本页仅显示前 100 项。",
    "Only the first 100 MOCs are shown.",
  ],
  "vault_save_settings.fa5c4872c5": [
    "确认保存预览中的规则",
    "Confirm and save previewed rules",
  ],
  "vault_save_settings.9110d26a8b": [
    "{p0} 请点“重新读取规则”核对保存状态。",
    "{p0} Click Reload rules to check the save status.",
  ],
  "vault_save_settings.b1356b33cb": [
    " 请点“重新读取规则”核对保存状态。",
    " Click Reload rules to check the save status.",
  ],
  "vault_save_settings.b9245f6f7a": [
    "保存规则已启用，将用于随后新准备的发布。",
    "Save rules enabled for future prepared publications.",
  ],
  "vault_save_settings.f475a9c95e": [
    "规则草稿已保存；随后新准备的发布继续使用独立文件夹。",
    "Rule draft saved. Future prepared publications continue using separate folders.",
  ],
  "vault_save_settings.9c7138fdc7": ["预览保存规则", "Preview save rules"],
  "vault_save_settings.4fda7b9a5c": ["新笔记", "New notes"],
  "vault_save_settings.e327e48f8a": ["归档笔记", "Archived notes"],
  "vault_save_settings.d0e935892f": ["专家 Draft 包", "Expert Draft packages"],
  "vault_save_settings.cdc151bf25": ["新建 MOC", "New MOCs"],
  "vault_save_settings.a1ff35230b": ["非图片原件", "Non-image originals"],
  "vault_save_settings.6a47290bf8": [
    "确认后启用此规则。",
    "Enable this rule after confirmation.",
  ],
  "vault_save_settings.7e004c7322": [
    "仅保存草稿，不启用。",
    "Save draft only; do not enable.",
  ],
  "vault_save_settings.a94adfc64c": [
    "保存时创建：{p0}",
    "Create on save: {p0}",
  ],
  "vault_save_settings.3579759511": [
    "所选目录已存在。",
    "Selected directories already exist.",
  ],
  "vault_save_settings.767cb6630d": [
    "目录选择说明",
    "Directory selection help",
  ],
  "vault_save_settings.88c054a0d9": [
    "逐级选择已有目录，或填写相对路径。新目录只在确认保存时创建。",
    "Choose existing directories level by level, or enter a relative path. New directories are created only after save confirmation.",
  ],
  "vault_save_settings.194a93bd70": [
    "当前规则已启用。更新时须重新勾选启用并预览；只保存草稿会停止新规则。",
    "Rules enabled. To update, select enable again and preview. Saving a draft alone stops the new rules.",
  ],
  "vault_save_settings.b6b6b69a99": [
    "当前未启用新规则。勾选启用并确认预览后，规则用于新准备的发布。已有笔记需单独预览迁移。",
    "New rules not enabled. Select enable and confirm the preview for future prepared publications. Existing notes need separate migration previews.",
  ],
  "vault_save_settings.00d0fa53d1": ["重新读取规则", "Reload rules"],
  "vault_save_settings.7b78aa2d34": ["关闭规则编辑", "Close rule editor"],
  "vault_save_settings.dad1f891a7": [
    "关闭会清除未保存的规则草稿，是否继续？",
    "Closing clears unsaved rule drafts. Continue?",
  ],
  "vault_save_settings.6f3062d825": [
    "之前选定的目录已变化，请重新选择并预览。",
    "Previously selected directory changed. Select and preview again.",
  ],
  "vault_save_settings.90c54f55da": [
    "上次创建目录未完成。请先在本机核对已创建的空目录，再结束这次操作并重新预览。",
    "Previous directory creation incomplete. Check created empty directories locally, then finish the operation and preview again.",
  ],
  "vault_save_settings.8ff1bc22f0": [
    "确认保留目录并结束待处理操作",
    "Keep directories and finish pending operation",
  ],
  "vault_save_settings.e2b6c28150": [
    "已保留目录并结束旧操作，请重新预览规则。",
    "Directories retained; old operation finished. Preview rules again.",
  ],
  "vault_save_settings.b5ee965460": [
    "此仓库只读，可查看目录，不能保存写入规则。",
    "Read-only vault. Directories can be viewed; write rules cannot be saved.",
  ],
  "vault_save_settings.3a45034d9a": [
    "整库合并（预览后明确确认）",
    "Vault merge (explicit confirmation after preview)",
  ],
  "vault_scope.fb93506fe5": ["保存规则使用的知识库", "Vault for save rules"],
  "vault_scope.eec6fac7d1": [
    "布局与外部改动使用的知识库",
    "Vault for layout and external changes",
  ],
  "vault_scope.77d28f8dda": [
    "读取规则不会保存、启用或移动文件。",
    "Loading rules does not save, enable, or move files.",
  ],
  "vault_scope.8c129578fa": [
    "暂无可用知识库，请先接入。",
    "No available vaults. Attach one first.",
  ],
  "vault_scope.f331b7a3d8": ["请选择知识库", "Select a vault"],
  "vaults.fa5e198203": ["移到废纸篓", "Move to Trash"],
  "vaults.52daa71ebc": ["目录", "Directory"],
  "vaults.750d5143a2": [
    "{p0} 个文件 · {p1} 字节。访问将撤销；系统恢复后须重新接入。",
    "{p0} files · {p1} bytes. Access will be revoked. Explicit attachment required after system restore.",
  ],
  "vaults.4fa4032b05": [
    "此目录处于同步位置，移动会影响同步副本。",
    "Directory is in a synced location. Moving affects synced copies.",
  ],
  "vaults.e8523c9140": [
    "将“{p0}”移到系统废纸篓？",
    "Move “{p0}” to system Trash?",
  ],
  "vaults.ba30d81f94": [
    "已移到系统废纸篓，旧授权保持撤销。",
    "Moved to system Trash. Old access remains revoked.",
  ],
  "vaults.d4ffae663f": ["管理 {p0}", "Manage {p0}"],
  "vaults.7599b9af1b": ["撤销访问或移动文件", "Revoke access or move files"],
  "vaults.cc00cf75b8": [
    "暂无启用的知识库。通过下方「接入笔记仓库」接入现有目录或新建仓库。",
    "No active vaults. Attach an existing directory or create a vault under Attach vault below.",
  ],
  "vaults.a726743441": [
    "待核对系统恢复结果",
    "System restore result needs checking",
  ],
  "vaults.3379e67bb0": ["重新接入并重建索引", "Reattach and rebuild index"],
  "vaults.72609868ff": ["核对系统恢复结果", "Check system restore result"],
  "vaults.23bbd25ce4": ["没有可核对的操作记录。", "No operations to check."],
  "vaults.44766b2487": [
    "确认已恢复原目录？",
    "Confirm original directory restored?",
  ],
  "vaults.519c6af8fa": [
    "先在系统废纸篓核对并恢复原目录。此步骤只核对目录身份，不重试移动。",
    "Check and restore the original directory in system Trash first. This step verifies directory identity only; it does not retry the move.",
  ],
  "vaults.5dcddfe561": ["确认已恢复原目录", "Confirm original restored"],
  "vaults.1d030d68c2": [
    "目录身份已核对，仍需显式重新接入和设置权限。",
    "Directory identity checked. Explicitly reattach and set permissions.",
  ],
  "vaults.dfb802238b": ["已启用", "Enabled"],
  "vaults.0a78c1808d": [
    "原件与来源版本独立保留",
    "Originals and source versions retained separately",
  ],
  "vaults.a716fbc7cb": ["默认参与查询", "Include in default queries"],
  "vaults.9cb0c5f70c": [
    "默认查询设置已保存，客户端权限保持独立。",
    "Default query settings saved. Client permissions remain separate.",
  ],
  "vaults.e60bd178ee": ["只读接入", "Read-only attachment"],
  "vaults.b00028289a": ["可读写", "Read / Write"],
  "vaults.626d68dcd2": ["解除接入", "Detach"],
  "vaults.2907713ef6": ["解除“{p0}”的接入？", "Detach “{p0}”?"],
  "vaults.0594975b79": [
    "保留原文件，撤销旧访问。重新使用须显式接入并授权。",
    "Keep original files and revoke old access. Reattach and authorize explicitly to use again.",
  ],
  "vaults.1302d10bbc": ["保留接入", "Keep attached"],
  "vaults.596bf74fc8": [
    "已解除接入，原文件保留；旧访问已撤销。",
    "Detached. Original files retained; old access revoked.",
  ],
  "vaults.b5aa919e9a": ["保存规则", "Save rules"],
  "vaults.cd997fb0c2": ["整理与布局迁移", "Organization & Layout Migration"],
};
