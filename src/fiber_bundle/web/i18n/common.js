import { requestErrors } from "../request_errors.js";
// UI message catalog; parameters preserve source data.
export default {
  "organization.catalog.wait": [
    "进度已更新；目录读取暂不可用，稍后自动重试。",
    "Progress updated; catalog temporarily unavailable. Retrying shortly.",
  ],
  "suppliers.nav": ["供应商信息", "Suppliers"],
  ...Object.fromEntries(
    Object.entries(requestErrors).map(([code, pair]) => [`api.${code}`, pair]),
  ),
  "read.busy": ["读取暂忙", "Read temporarily busy"],
  "read.changed": ["读取期间数据已变化", "Data changed during the read"],
  "atlas.theme.description": [
    "独立界面 · 知识、连接与系统",
    "Complete interface · knowledge, connections and system",
  ],
  "atlas.role.unknown": [
    "尚无独立服务状态",
    "Independent service status not recorded",
  ],
  "atlas.role.unconfirmed": ["状态尚未确认", "Status not confirmed"],
  "atlas.receipt.held": [
    "{p0} 批回执待核对",
    "{p0} receipt batches need reconciliation",
  ],
  "atlas.receipt.pending": [
    "{p0} 批回执待发送或更新",
    "{p0} receipt batches awaiting send or update",
  ],
  "atlas.receipt.empty": ["无待发送回执", "No pending receipts"],
  "atlas.receipt.off": ["新回执已关闭", "New receipts disabled"],
  "operation.directory.wait": [
    "{p0}：请在系统窗口中选择文件夹或取消；尚未保存设置。",
    "{p0}: choose a folder or cancel in the system window. Settings have not been saved.",
  ],
  "backup.snapshot.busy": [
    "正在创建一致性备份，请稍后重试。",
    "Creating a consistent backup. Please retry shortly.",
  ],
  "api.450e10e5e8": [
    "请求未获许可，请刷新会话后重试。",
    "Request not authorized. Refresh the session and retry.",
  ],
  "api.50d9ba5f03": [
    "资源不可用或权限已撤销，请刷新。",
    "Resource unavailable or access revoked. Refresh.",
  ],
  "api.73cb0d1c34": [
    "当前记录已变化，请刷新并核对后重试。",
    "This record changed. Refresh and check it before retrying.",
  ],
  "api.ae7b90e913": [
    "输入不符合要求，请检查名称与权限。",
    "Invalid input. Check names and permissions.",
  ],
  "api.2a462fc4a4": [
    "登录尝试过多，请稍后重试。",
    "Too many login attempts. Try again later.",
  ],
  "api.62927f0c06": [
    "后台存储暂不可用，请检查本机后台。",
    "Backend storage unavailable. Check the local backend.",
  ],
  "api.a3562bc1ea": ["读取已取消。", "Read canceled."],
  "api.9eaa52a84e": [
    "本次读取超时，请稍后刷新。",
    "Read timed out. Refresh later.",
  ],
  "api.c869a6bb4e": [
    "无法连接本机后台，请确认后台已启动后刷新。",
    "Cannot connect to the local backend. Start it, then refresh.",
  ],
  "api.b76dccae01": [
    "会话已变化，请刷新后重试。",
    "Session changed. Refresh and retry.",
  ],
  "api.b5aa63f8ff": [
    "密码不正确，或尚未设置管理密码。",
    "Incorrect password, or no admin password has been set.",
  ],
  "api.c3ea8f8256": [
    "会话已失效，请重新登录。",
    "Session expired. Sign in again.",
  ],
  "api.a47ad1a311": ["事件内容冲突。", "Event content conflict."],
  "api.3a60f3ca05": [
    "状态目录含未分类资料，备份准备已阻止。请先核对备份范围；不要删除原件。",
    "Backup preparation blocked: the state directory contains unclassified sources. Check the backup scope first; do not delete originals.",
  ],
  "api.1b6bb1c96e": [
    "候选已保留：完整段落、代码块或总输出超过冻结发布限额。请调整新任务额度后重新导入；不会截断发布。",
    "Candidate retained: complete paragraphs, code blocks, or total output exceed the frozen publication limit. Adjust limits for a new task and import again. Publication will not truncate content.",
  ],
  "api.ecc9a91696": [
    "候选已保留：跨段引用、文内锚点或 HTML 无法安全分片。请使用足够容纳单文件的新任务额度。",
    "Candidate retained: cross-section references, anchors, or HTML cannot be split safely. Use a new task limit large enough for one file.",
  ],
  "api.8ae42930eb": [
    "笔记已变化，全文索引需要更新。更新完成前不会交付旧内容。",
    "Notes changed. Update the full-text index. Old content will not be delivered until the update completes.",
  ],
  "api.83859c15eb": [
    "匹配内容过多，请缩小知识库范围或补充更具体的关键词。",
    "Too many matches. Narrow the vault scope or use more specific terms.",
  ],
  "api.4532a19536": [
    "操作未完成，请刷新后重试。",
    "Operation incomplete. Refresh and retry.",
  ],
  "api.ca780faf06": ["后台响应格式无效。", "Invalid backend response format."],
  "api.eb175157b4": [
    "未能确认本机废纸篓请求结果。请先检查原目录与系统废纸篓，再刷新核对；不要连续重复删除。",
    "Cannot confirm the Trash request result. Check the original directory and system Trash, then refresh. Do not repeatedly delete.",
  ],
  "api.3f7376d212": [
    "会话已变化，请刷新后核对操作状态。",
    "Session changed. Refresh and check the operation status.",
  ],
  "api.1b1a5667de": [
    "废纸篓操作未完成，请刷新后核对目录状态。",
    "Trash operation incomplete. Refresh and check the directory.",
  ],
  "app.a8a0a51ff2": ["已切换为{p0}。", "Switched to {p0}."],
  "app.d5957c1fe9": ["管理接口可达", "Admin API reachable"],
  "app.56bf9d2b44": ["管理接口连接失败", "Cannot reach admin API"],
  "app.9f3db13ddd": [
    "会话已失效，请在当前页面重新登录；刷新会清除暂存的文本草稿。",
    "Your session expired. Sign in here to keep text drafts; refreshing will clear them.",
  ],
  "app.e7188508c2": [
    "请登录以管理知识库和客户端。",
    "Sign in to manage knowledge bases and clients.",
  ],
  "app.971f7f3e10": ["页面错误", "Page error"],
  "app.8753cb4c8a": [
    "页面发生错误，请刷新后重试。",
    "The page encountered an error. Refresh and try again.",
  ],
  "app.b845364341": ["未处理的异步错误", "Unhandled async error"],
  "app.ac76aa14d0": [
    "操作未完成，请检查后台连接后重试。",
    "The action did not finish. Check the backend connection and try again.",
  ],
  "app.7072f52cbc": [
    "批次加载失败，请稍后重试。",
    "Batches could not be loaded. Try again later.",
  ],
  "app.a5930e0b6e": ["正在连接管理接口", "Connecting to admin API"],
  "app.8be8a8e8f1": [
    "已重新登录，文本草稿已还原。请核对表单选项；文件与凭据需重新选择。",
    "Signed in again. Text drafts were restored; check form selections and select files and credentials again.",
  ],
  "app.5a6ab4b41b": ["已退出登录。", "Signed out."],
  "app.d00dc255b9": [
    "该知识库当前不可用，请核对接入状态与权限。",
    "This knowledge base is unavailable. Check its connection and permissions.",
  ],
  "app.e1e0df8312": [
    "该记录已不在当前列表或权限范围内，请核对来源与筛选条件。",
    "This record is outside the current list or access scope. Check its source and filters.",
  ],
  "app.d0c561747b": [
    "会话已到期，请在当前页面重新登录；刷新或关闭会清除暂存的文本草稿。",
    "Your session expired. Sign in here; refreshing or closing will clear temporary text drafts.",
  ],
  "appearance.432121b96f": ["中文预览", "Chinese preview"],
  "appearance.45937b7f28": ["中文标题", "Chinese heading"],
  "approvals.fa9f7e18f2": [
    "没有待审批的空库。",
    "No empty vaults awaiting approval.",
  ],
  "approvals.be05ec8f50": ["申请方：{p0}", "Applicant: {p0}"],
  "approvals.8cc1f14451": ["已不可用", "Unavailable"],
  "approvals.00c4392c9e": ["{p0} 授权客户端", "{p0} authorized client"],
  "approvals.293ff43cb5": ["批准并启用", "Approve and enable"],
  "approvals.988276003d": [
    "请先创建有效客户端。",
    "Create a valid client first.",
  ],
  "backend.38506bec99": ["管理后台运行中", "Admin backend running"],
  "backend.bce7d1befc": ["管理后台：{p0}", "Admin backend: {p0}"],
  "backend.4aad08c81e": [
    "可独立停止这份后台；关闭管理窗口会保留后台。",
    "You can stop this backend independently. Closing the admin window leaves it running.",
  ],
  "backend.41610b6ea3": [
    "此后台未由桌面生命周期命令启动。",
    "This backend was not started by the desktop lifecycle command.",
  ],
  "backend.a6a2f0eccc": [
    "项目消息入口：{p0} / {p1} 个平台连接已建立。观测时间 {p2}。",
    "Project message intake: {p0} / {p1} platform connections established. Observed at {p2}.",
  ],
  "backend.3e6424b737": [
    "查看飞书 / 微信与 Hermes 状态",
    "View Feishu / WeChat and Hermes status",
  ],
  "backend.f6337d25b4": ["交互任务排队", "Interactive task queue"],
  "backend.68ac5e590a": ["重任务排队", "Heavy task queue"],
  "backend.8eaa29c22a": ["{p0} 个", "{p0} items"],
  "backend.7450cc5b16": ["未记录", "Not recorded"],
  "backend.842fd42233": [
    "每日摘要 {p0} · {p1} · {p2} 项",
    "Daily digest {p0} · {p1} · {p2} items",
  ],
  "backend.60823aaec7": ["已发送", "Sent"],
  "backend.4af13bb1ef": [
    "发送结果未知，请人工核对；不会自动重发",
    "Delivery result unknown. Check manually; no automatic resend",
  ],
  "backend.6f2d21bb39": [
    "当前安装版本 {p0} · 候选 schema {p1}",
    "Installed version {p0} · Candidate schema {p1}",
  ],
  "backend.eb476f3c7b": [
    "执行配置采用不可变版本；保存更新仅供新任务选用。",
    "Execution configurations are immutable versions. Saved updates are available only to new tasks.",
  ],
  "backend.a0da867f78": [
    "查看组件版本与校验摘要",
    "View component versions and verification summary",
  ],
  "backend.4821fdc58c": ["固定来源", "Pinned source"],
  "backend.6c6b16d6ef": [
    "检查已安装组件版本",
    "Check installed component versions",
  ],
  "backend.3891f8575a": [
    "已安装代码与固定来源摘要一致。",
    "Installed code matches the pinned source digest.",
  ],
  "backend.ec604d27a3": [
    "检测到组件变化，请保留任务并核对候选版本。",
    "Component changes detected. Keep the task and check the candidate version.",
  ],
  "backend.4532bdd3d8": [
    "此检查不联网、不核验本机 ASR 权重；版本安装由独立发行步骤处理，运行任务仍使用原绑定。",
    "This check is offline and does not validate local ASR weights. Version installation is a separate release step. Running tasks keep their original bindings.",
  ],
  "backend.9a5aa3829b": [
    "仅待确认批次进入每日摘要；关闭提醒不删除待办。",
    "Only batches awaiting confirmation enter the daily digest. Disabling reminders does not remove pending work.",
  ],
  "backend.ca767767e0": [
    "暂无待确认批次。",
    "No batches awaiting confirmation.",
  ],
  "backend.d76e4220b9": ["每日批次提醒", "Daily batch reminders"],
  "backend.4deb0f9d95": ["{p0} 条资料 · {p1}", "{p0} sources · {p1}"],
  "backend.86316e92b4": ["保存提醒偏好", "Save reminder preferences"],
  "backend.f3b7dbbd73": ["提醒偏好已保存。", "Reminder preferences saved."],
  "backend.6eb2017d5f": ["停止这份管理后台？", "Stop this admin backend?"],
  "backend.4c2e5b7db1": [
    "停止这份管理后台，并取消受控查询的后续处理。已发生费用和原件会保留。",
    "Stop this backend and cancel further managed-query processing. Existing costs and originals are retained.",
  ],
  "backend.d67c4d11f3": [
    "独立微信接收进程须另行管理。",
    "Manage the separate WeChat receiver independently.",
  ],
  "backend.14534833df": ["停止管理后台", "Stop admin backend"],
  "backend.994e1508ce": [
    "后台正在停止。可在本机启动后使用顶部“重新连接”。",
    "Backend is stopping. Start it locally, then use Reconnect at the top.",
  ],
  "backups.ed53c87606": [
    "已准备，等待离线执行",
    "Prepared for offline execution",
  ],
  "backups.8a63f9f4dc": [
    "完成标记已记录，尚未校验备份",
    "Completion marker recorded; backup not yet validated",
  ],
  "backups.989bb1513e": [
    "未完成，保留现场供核查",
    "Incomplete; retained for inspection",
  ],
  "backups.4680ea6019": [
    "已恢复并核验，等待单主机启用",
    "Restored and verified; awaiting single-host activation",
  ],
  "backups.2db4402e38": ["恢复进行中", "Restoring"],
  "backups.a5f96df73b": [
    "恢复失败，禁止启动",
    "Restore failed; startup blocked",
  ],
  "backups.f51e0fe8d7": ["已在指定主机启用", "Activated on the selected host"],
  "backups.ceef0acb49": ["源主机已停用", "Source host disabled"],
  "backups.e6f4cc3a9e": ["暂无操作记录。", "No activity yet."],
  "backups.403e95ec2a": ["完整数据备份", "Full data backup"],
  "backups.9422e7ad01": ["干净位置恢复", "Restore to a clean location"],
  "backups.8c655a5021": ["状态未知，请核查", "Unknown status; check manually"],
  "backups.cd883342cd": ["位置：{p0}", "Location: {p0}"],
  "backups.b94d3a0681": [
    "先停止管理后台、微信接收及全部任务进程，再在终端执行以下命令。使用非默认配置时追加 --config 实际配置文件。",
    "Stop the admin backend, WeChat receiver, and all task processes before running these commands in Terminal. For a non-default setup, append --config followed by the actual config file.",
  ],
  "backups.edf4af7150": [
    "备份密码只在终端隐藏输入。请另行妥善保存，丢失后不能解密。",
    "Enter the backup password privately in Terminal. Keep it separately; a lost password prevents decryption.",
  ],
  "backups.347fb279b0": [
    "列表仅核对完成标记和文件元数据。完整哈希校验请在终端执行；恢复仍须验证密码和全部原件。",
    "This list checks completion markers and file metadata only. Run full hash validation in Terminal. Restore still requires the password and all originals to be verified.",
  ],
  "backups.898033e017": ["准备干净恢复", "Prepare clean restore"],
  "backups.d6d1947da7": [
    "恢复操作已准备；不会覆盖或启动现有项目。",
    "Restore prepared. Existing projects will not be overwritten or started.",
  ],
  "backups.ab24c10690": [
    "恢复保留费用与历史记录，客户端访问和待执行任务已停用。重配凭证并核对源机停用后，按恢复说明启用；不会自动发送或调用模型。",
    "Restore preserves costs and history; client access and pending tasks are disabled. Reconfigure credentials, check that the source host is disabled, then follow the restore instructions to activate. Nothing is sent and no model is called automatically.",
  ],
  "backups.80eb096d10": [
    "已完成与历史操作",
    "Completed and historical operations",
  ],
  "backups.bb092627cf": [
    "备份操作已准备；离线执行后才会生成备份。",
    "Backup prepared. The backup is created only after offline execution.",
  ],
  "command_palette.db98bb533a": ["查询知识库内容", "Search knowledge base"],
  "command_palette.487e0462ef": [
    "搜索 笔记 全文 search query",
    "search notes full text query",
  ],
  "command_palette.6af5f8d45e": ["普通资料导入", "Import sources"],
  "command_palette.2f2257c7d8": ["import 文件 上传", "import files upload"],
  "command_palette.0c6f157dc1": [
    "fake-expert 专用导入",
    "Import with fake-expert",
  ],
  "command_palette.68c4eac7f8": ["专家 pdf", "expert pdf"],
  "command_palette.141690d8db": ["打开收件箱", "Open inbox"],
  "command_palette.c94752e88f": ["inbox 待办 审核", "inbox tasks review"],
  "command_palette.6de345906b": ["搜索页面与操作", "Search pages and actions"],
  "command_palette.6c9df66b30": [
    "输入页面或操作名称…",
    "Type a page or action…",
  ],
  "command_palette.2829ef8800": [
    "只搜索页面与操作；笔记检索使用现有查询入口。",
    "Searches pages and actions. Use knowledge search to find notes.",
  ],
  "command_palette.70cde5ca61": [
    "没有匹配的页面或操作。",
    "No matching pages or actions.",
  ],
  "directory_picker.74966e20df": ["取消选择", "Cancel selection"],
  "directory_picker.0b14bf729f": [
    "正在定位当前目录…",
    "Locating current directory…",
  ],
  "directory_picker.e6bf8442f8": [
    "请选择保存目录。",
    "Select a save directory.",
  ],
  "directory_picker.512cc21120": [
    "已定位当前目录。展开箭头可浏览下级，点击“选择”确认。",
    "Current directory located. Expand arrows to browse children; click Select to confirm.",
  ],
  "directory_picker.fcf17981b6": [
    "当前路径不存在或不在可访问范围内，原路径保留。可选择其他目录，或保留新路径后预览创建。",
    "Current path is missing or outside the accessible scope. Original path retained. Choose another directory, or keep the new path and preview its creation.",
  ],
  "directory_picker.1696526872": [
    "当前相对路径格式需要核对。原输入保留，可从目录树重新选择。",
    "Check the relative path format. Original input retained; select again from the directory tree.",
  ],
  "directory_picker.cd7d0859b2": [
    "目录定位未完成：{p0} 原路径保留，可点击刷新重试。",
    "Directory location incomplete: {p0} Original path retained. Refresh to retry.",
  ],
  "directory_picker.390633141d": [
    "刷新目录并定位",
    "Refresh and locate directory",
  ],
  "directory_picker.eb0626f262": ["选择{p0}", "Select {p0}"],
  "directory_picker.5cd7012288": ["当前路径：{p0}", "Current path: {p0}"],
  "directory_picker.c5768ca972": ["尚未选择", "Not selected"],
  "execution.7dec8f209e": [
    "模型请求与费用明细（{p0}）",
    "Model requests and cost details ({p0})",
  ],
  "execution.d31bc19717": ["尚无模型请求记录。", "No model requests recorded."],
  "execution.c98e118e0a": ["模型", "Model"],
  "execution.6320b4a872": ["状态", "Status"],
  "execution.2f95feba9e": ["预留", "Reserved"],
  "execution.9256e24bcc": ["已计费", "Charged"],
  "execution.8cc40209c5": ["计费日", "Billing date"],
  "execution.4e20d7d9ce": ["完整账本记录", "Full ledger record"],
  "execution.31306cdf84": [
    "暂无执行任务或模型费用。",
    "No execution tasks or model costs.",
  ],
  "execution.eee4c4c273": [
    "任务上限 {p0} USD；尝试 {p1}",
    "Task limit {p0} USD; attempt {p1}",
  ],
  "execution.d5d1776a0c": [
    "未结算或未知请求：{p0}；预留与原记录保留。",
    "Unsettled or unknown requests: {p0}; reservations and original records retained.",
  ],
  "execution.c26fb419db": ["技术详情", "Technical details"],
  "execution.375680dd41": ["取消当前执行", "Cancel current execution"],
  "execution.61d383a5f5": ["取消此执行？", "Cancel this execution?"],
  "execution.c281daad48": [
    "原件、已知费用和未知结果会保留。",
    "Originals, known costs, and unknown results retained.",
  ],
  "execution.0a6f30fae5": ["取消执行", "Cancel execution"],
  "execution.121fd8a010": ["保留执行", "Keep execution"],
  "execution.e17003e5fe": ["对账中断执行", "Reconcile interrupted execution"],
  "execution.77636a713f": [
    "对账中断执行？",
    "Reconcile interrupted execution?",
  ],
  "execution.695fe4582a": [
    "仅对账已失去负责人的执行，未知请求不会重放。",
    "Only executions without an owner are reconciled. Unknown requests are not replayed.",
  ],
  "execution.cd629eb946": ["开始对账", "Start reconciliation"],
  "font_picker.8351149f76": ["筛选本机字体…", "Filter installed fonts…"],
  "font_picker.f7a6a2b848": ["筛选中文字体", "Filter Chinese fonts"],
  "font_picker.91c37cb4b0": ["筛选英文字体", "Filter English fonts"],
  "font_picker.b40da9b3cf": ["本机已安装字体", "Installed fonts"],
  "font_picker.05244e6861": [" · 最近选择", " · Recently selected"],
  "font_picker.1e314e2701": [
    "本机未找到，使用回退",
    "Not found locally; using fallback",
  ],
  "font_picker.f72b16fa2b": [
    "正在读取本机已安装字体…",
    "Reading installed fonts…",
  ],
  "font_picker.864eb02b30": [
    "当前系统不支持读取本机字体。内置字体仍可选择。",
    "Installed-font discovery is unsupported on this system. Bundled fonts remain available.",
  ],
  "font_picker.945874478c": [
    "已读取 {p0} 个本机字体家族，中英文可分别选择。缺少的字形由内置字体补足。",
    "Loaded {p0} installed font families. Choose Chinese and English fonts separately; bundled fonts supply missing glyphs.",
  ],
  "font_picker.3744b387fe": ["刷新本机字体", "Refresh installed fonts"],
  "font_picker.ec52a42b02": [
    "读取本机字体超时，请重试。现有字体选择保留。",
    "Installed-font discovery timed out. Try again; your selection is retained.",
  ],
  "font_picker.5704dda9ee": [
    "暂时无法读取本机字体，请点击“读取本机字体”重试。现有字体选择保留。",
    "Cannot read installed fonts now. Choose “Read installed fonts” to retry. Your selection is retained.",
  ],
  "note_index_errors.f910d4b699": [
    "普通笔记或 Map 的文件身份已变化。请到知识库的“整理与布局迁移 → 批量核对外部改动”中核对位置并保留原身份，再更新索引；无需重新入库。",
    "Note or Map file identity changed. In Vaults → Organization & Layout Migration → Review external changes, check locations and retain identities, then update the index. No re-import required.",
  ],
  "note_index_errors.8abee9626a": [
    "部分已发布笔记已移动、改名、替换或缺失。请到知识库的“整理与布局迁移 → 批量核对外部改动”中核对位置后，再更新索引。",
    "Some published notes moved, renamed, replaced, or missing. Check locations in Vaults → Organization & Layout Migration → Review external changes before updating the index.",
  ],
  "note_index_errors.105b6ed8f8": [
    "笔记索引已过期，请更新此知识库索引。",
    "Note index stale. Update this vault's index.",
  ],
  "note_index_errors.a830e79836": [
    "存在尚未完成的位置调整，请先在知识库整理面板恢复该操作，再更新索引。",
    "Location changes are incomplete. Recover the operation in Vaults before updating the index.",
  ],
  "operation_feedback.81dd6cd5a0": [
    "{p0}：等待后台确认。离页不会取消已提交操作。",
    "{p0}: awaiting backend confirmation. Leaving does not cancel a submitted operation.",
  ],
  "operation_feedback.d8179024e1": [
    " 已等待 {p0} 秒。",
    " Waited {p0} seconds.",
  ],
  "page_context.9119bdd976": ["保存与整理", "Save & organize"],
  "page_context.f6908750f5": [
    "先接入已有仓库，再配置六类目录和处理身份。MOC、自动策略及整库合并各自保留预览与确认。",
    "Attach an existing vault, then configure six directory roles and processing identity. MOCs, automatic policy, and vault merge retain separate preview and confirmation.",
  ],
  "page_context.9b61f33a45": ["从原件到候选", "From originals to candidates"],
  "page_context.c0c8fac0f4": [
    "选择身份、目标知识库和提取方式。普通资料与 fake-expert 分别处理；保存原件、生成 Draft 和正式发布是独立阶段。",
    "Choose identity, target vault, and extraction method. Ordinary sources and fake-expert are separate. Saving originals, generating Drafts, and publication are distinct stages.",
  ],
  "page_context.b040552c3f": [
    "集中处理，保留原始证据",
    "One worklist, with original evidence",
  ],
  "page_context.48ae17a05d": [
    "按来源查看当前待办。进入对应记录后完成审批、补充、审核或核对，不在此页批量自动放行。",
    "View pending work by source. Open a record to approve, supplement, review, or check it. This page does not automatically approve batches.",
  ],
  "page_context.d864ebfd4f": [
    "知识库接入审批",
    "Knowledge-base access approval",
  ],
  "page_context.373de641d8": [
    "核对申请方、目标库与授予权限，再批准启用。",
    "Check the applicant, target knowledge base and permissions before approving.",
  ],
  "page_context.f759ca7646": ["选择批次处理方式", "Choose batch processing"],
  "page_context.6899e7700a": [
    "收件缓冲与等待本人选择分开显示。冻结后选择处理方式；未知结果先核对，不重发。",
    "Collection and pending choices are separate. Choose processing after the batch freezes. Verify unknown outcomes before resending.",
  ],
  "page_context.2962560e79": ["文档与文章审核", "Document & article review"],
  "page_context.075ffa02af": [
    "核对完整来源及候选中的内容、图片和数字，再记录实际判断。执行完成不代表已经发布。",
    "Check the full source, candidate content, images and numbers before recording your review. Execution does not imply publication.",
  ],
  "page_context.a5b82bcb27": ["音频与画面审核", "Audio & visual review"],
  "page_context.3ca03c6f06": [
    "核对完整音视频、转录覆盖和术语疑点。抽样帧不能替代完整画面审核；保留人工审核接口。",
    "Check the full media, transcript coverage and uncertain terms. Sampled frames do not replace full visual review.",
  ],
  "page_context.2e35c9c8cb": [
    "收件、绑定与连接",
    "Intake, bindings & connections",
  ],
  "page_context.e9f5e1f060": [
    "分别查看飞书/微信绑定、Hermes 进程、心跳及传输。暂停入口不影响已有知识；解绑与删除需明确操作。",
    "Check Feishu / WeChat pairing, Hermes process, heartbeat, and transport separately. Pausing preserves existing knowledge; unlinking and deletion require explicit action.",
  ],
  "page_context.28357d3697": [
    "Agent 接入与客户端权限",
    "Agent & client permissions",
  ],
  "page_context.6828b931c8": [
    "先选择 Agent 连接或客户端权限。每个身份独立授权；配置、工具可用及问答结果分别验收。",
    "Choose an Agent connection or client permissions. Each identity has separate grants. Verify configuration, tools, and answer results separately.",
  ],
  "page_context.df6a8e186c": [
    "配置路由，按需验证",
    "Configure routing and test explicitly",
  ],
  "page_context.687dedd1fe": [
    "保存模型与预算后，明确发起测试。配置存在或历史调用成功不代表当前可用；首页不会自动调用模型。",
    "Save models and budgets, then explicitly run a test. Configuration and past success do not prove current availability. Home does not call models automatically.",
  ],
  "page_context.cf159352e2": [
    "执行记录与费用口径",
    "Execution records & cost scope",
  ],
  "page_context.593d003186": [
    "执行、取消和未知结果核对在下方处理。云费用是配置单价估算；本地调用记录独立计数。",
    "Manage execution, cancellation, and unknown results below. Cloud costs use configured-rate estimates; local calls are counted separately.",
  ],
  "page_context.ffacbb60d6": [
    "原件、版本与处理配置",
    "Originals, versions & processing",
  ],
  "page_context.8fb6b18cbf": [
    "按身份和知识库读取资料版本。新资料统一从独立“资料导入”入口进入；组件清单不代表服务在线。",
    "Read source versions by identity and vault. Import new sources through Import Sources. A component list does not confirm live service availability.",
  ],
  "page_context.01c511615d": ["笔记关联与 MOC", "Note connections & MOCs"],
  "page_context.501f267ef4": [
    "配置自动归类并复核疑难，或手动调整笔记与专家 Skill 的 MOC。高级关联分析保留为补充。",
    "Configure automatic classification and review exceptions, or manually adjust note and expert Skill MOCs. Advanced association analysis remains available.",
  ],
  "page_context.8942d5357b": [
    "有来源的文档制作",
    "Source-grounded document creation",
  ],
  "page_context.8039495b36": [
    "先准备来源与任务，确认预算后执行。模型草稿、人工确认和保存结果分别记录。",
    "Prepare sources and a task, then confirm budget before execution. Model drafts, manual confirmation, and saved results are recorded separately.",
  ],
  "page_context.6d07ccaf55": ["后台运行与提醒", "Backend & reminders"],
  "page_context.293a33d576": [
    "核对当前实例与恢复状态。桌面窗口和后台独立运行；提醒与启动操作由你明确发起。",
    "Check the current instance and recovery state. Desktop window and backend run independently. Reminders and startup require explicit action.",
  ],
  "page_context.46396506e6": [
    "备份、恢复与分发",
    "Backup, restore & distribution",
  ],
  "page_context.9450b17c26": [
    "先准备完整备份范围，再在终端输入加密密码。完成标记不替代密文与恢复验证。",
    "Prepare the full backup scope, then enter the encryption password in Terminal. Completion markers do not replace archive and restore verification.",
  ],
  "page_context.6710226f6d": ["本机显示与会话", "Local display & sessions"],
  "page_context.16007467e2": [
    "调整主题、字体和字号；安全设置按现有确认保存。显示偏好不改变模型、通道或知识库权限。",
    "Adjust themes, fonts, and sizes. Security settings keep their existing confirmation steps. Display preferences do not alter model, channel, or vault permissions.",
  ],
  "select_controls.558f727c56": ["选择选项", "Choose an option"],
  "select_controls.da590a8fe3": ["请选择", "Select…"],
  "session.c63e9bcc68": [
    "后台会话响应无效。",
    "Invalid backend session response.",
  ],
  "session_expiry.e034d1e689": [
    "登录将在 {p0} 分钟内到期。请及时保存当前内容；到期后可重新登录继续。",
    "Session expires within {p0} minutes. Save your work soon. Sign in again after expiry to continue.",
  ],
  "settings.4db631dd16": [
    "外观已保存，适用于当前浏览器或桌面客户端。",
    "Appearance saved for this browser or desktop client.",
  ],
  "settings.a91a314b0d": [
    "无法保存外观偏好，请检查浏览器存储权限。当前预览仍保留。",
    "Could not save appearance preferences. Check client storage permissions. Your preview is retained.",
  ],
  "settings.41c785deb2": ["已还原为保存的外观。", "Saved appearance restored."],
  "settings.c7a4aee7f0": [
    "正在预览默认字体与显示设置。当前主题保留；保存后生效。",
    "Previewing default fonts and display settings. Current theme is retained; save to apply.",
  ],
  "settings.e38db8dad1": [
    "登录时长已保存，下次登录生效；当前会话到期时间保持不变。",
    "Login duration saved for the next sign-in. The current session expiry is unchanged.",
  ],
  "settings.9ef4b305cb": [
    "设置已在其他窗口更新，请重新进入本页核对后再保存。",
    "Settings changed in another window. Reopen this page and check before saving.",
  ],
  "settings.4394830f2b": [
    "{p0} 秒（启动配置）",
    "{p0} seconds (startup setting)",
  ],
  "settings.45a632cfb6": [
    "当前会话到期：{p0}",
    "Current session expires: {p0}",
  ],
  "sidebar.bb0b6e36b3": ["收起侧栏", "Collapse sidebar"],
  "sidebar.658ff1eade": ["展开侧栏", "Expand sidebar"],
  "state_labels.9344597efd": ["执行失败", "Execution failed"],
  "state_labels.1f4691c297": ["执行中断", "Execution interrupted"],
  "state_labels.27e1e84f07": ["待提取", "Awaiting extraction"],
  "state_labels.fd014ccaac": ["提取中", "Extracting"],
  "state_labels.161d4d8e3b": ["已提取，待审核", "Extracted · Awaiting review"],
  "state_labels.a1d66d5b15": ["提取失败", "Extraction failed"],
  "state_labels.c4892a4060": ["提取中断", "Extraction interrupted"],
  "state_labels.d84ecf74a2": [
    "处理完成，待审核",
    "Processed · Awaiting review",
  ],
  "state_labels.6fcbae1d92": ["发布已准备", "Publication prepared"],
  "state_labels.b7a0e0aad1": ["发布中", "Publishing"],
  "state_labels.68db41a849": ["发布冲突", "Publication conflict"],
  "state_labels.926f4d9240": ["未发布", "Not published"],
  "state_labels.adaaf9a167": ["来源处理", "Source processing"],
  "state_labels.e9cf96024c": ["笔记查询", "Note search"],
  "state_labels.a8db5e025f": ["执行任务 · {p0}", "Execution task · {p0}"],
  "state_labels.087741c80f": ["执行任务 · ", "Execution task · "],
  "state_labels.ba99678306": ["已预留", "Reserved"],
  "state_labels.374d13aec7": ["已结算", "Settled"],
  "state_labels.fb7e71452c": ["结果未知", "Outcome unknown"],
  "theme_picker.07a79a5de6": ["选择主题", "Choose a theme"],
  "theme_picker.da0b2cc5a9": ["关闭主题选择", "Close theme picker"],
  "theme_picker.e408a124d3": [
    "点击卡片切换当前外观；设为默认后，下次启动或刷新使用该主题。字体与字号保留。",
    "Choose a card to change the current appearance. Set it as default to use it next time. Fonts and sizes are retained.",
  ],
  "theme_picker.4dfa069901": [
    "当前外观已切换；默认主题保持不变。",
    "Current appearance changed. Default theme is unchanged.",
  ],
  "theme_picker.d91f19fd14": [
    "无法切换主题，请重试。未保存内容已保留。",
    "Could not switch theme. Try again; unsaved content is retained.",
  ],
  "theme_picker.3815f99584": ["研究笔记", "Research notes"],
  "theme_picker.3e4c524087": [
    "整理资料，随时查阅。",
    "Organize sources. Find them when needed.",
  ],
  "theme_picker.03c7d85bfe": ["✓ 已入库", "✓ Saved"],
  "theme_picker.77bbbb5ddf": ["✓ 当前", "✓ Current"],
  "theme_picker.088d6e4942": ["默认主题", "Default theme"],
  "theme_picker.bae4baeb9c": ["设为默认主题", "Set as default"],
  "theme_picker.c3f2a26cab": [
    "已将{p0}设为默认主题。",
    "{p0} is now the default theme.",
  ],
  "theme_picker.11a33681be": [
    "无法保存默认主题，请检查浏览器存储权限后重试。当前外观与未保存内容已保留。",
    "Could not save the default theme. Check storage permissions and try again. Current appearance and unsaved content are retained.",
  ],
  "theme_picker.61dc697e60": [
    "默认：{p0}。仅适用于当前浏览器或桌面客户端。",
    "Default: {p0}. Applies to this browser or desktop client only.",
  ],
  "themes.be65fe2902": ["暖纸 · Oracle", "Oracle · Warm paper"],
  "themes.bfb2f44e85": [
    "暖纸分区、炭灰操作与衬线展示标题",
    "Warm paper sections, charcoal actions and serif headings",
  ],
  "themes.faad46b2f7": ["极简 · Tesla", "Tesla · Minimal"],
  "themes.1839e312ec": [
    "白灰空间、轻量层级与清晰的蓝色操作",
    "White and gray surfaces, light hierarchy and clear blue actions",
  ],
  "themes.7e24a44154": ["暖米 · Cursor", "Cursor · Warm ivory"],
  "themes.0510135ca9": [
    "奶油底色、墨色文字与克制的橙色强调",
    "Cream surfaces, ink text and restrained orange accents",
  ],
  "themes.ebe1d4b5bf": ["暖陶 · Clay", "Clay · Warm terracotta"],
  "themes.4688cd1a91": [
    "暖灰底色、陶土操作与柔和分层阴影",
    "Warm gray surfaces, terracotta actions and soft shadows",
  ],
  "themes.29edc1452e": ["暗色", "Dark"],
  "themes.7e56063c7e": [
    "近黑表面、冰蓝操作与柔和的状态色",
    "Near-black surfaces, ice-blue actions and quiet status colors",
  ],
  "ui.bcd6771e08": ["查询", "Search"],
  "ui.1503fb8c08": ["取原件", "Retrieve original"],
  "ui.0006d696d8": ["新增", "Add"],
  "ui.d447039092": ["权限已保存。", "Permissions saved."],
  "ui_updates.b53533de47": [
    "后台已更新。保存当前草稿后，重新载入界面以使用新版本。",
    "Backend updated. Save current drafts, then reload the interface for the new version.",
  ],
  "ui_updates.8352fd0333": ["重新载入界面", "Reload interface"],
  "unsaved_changes.6badf64a4d": [
    "本页有未保存的内容，离开可能会清除这些更改。确定离开？",
    "This page has unsaved changes. Leaving may clear them. Leave?",
  ],
  "usage.adc87185bb": ["正在更新用量…", "Updating usage…"],
  "usage.5e7e462263": ["全部 API Key", "All API keys"],
  "usage.8c46f73a3b": [" · 当前配置", " · Current configuration"],
  "usage.f211a8767f": ["{p0} 条费用待确认", "{p0} costs need confirmation"],
  "usage.fca246601e": [
    "缺少价格 {p0} 条 · 处理中 {p1} 条 · 结果未知 {p2} 条",
    "{p0} missing prices · {p1} in progress · {p2} unknown results",
  ],
  "usage.f1057e8154": [
    "API Key / 凭据配置",
    "API key / Credential configuration",
  ],
  "usage.55405afaf8": ["估算费用 · USD", "Estimated cost · USD"],
  "usage.56b00fe769": ["待确认预留 · USD", "Unconfirmed reservation · USD"],
  "usage.2d48ae458d": ["按 API Key 汇总", "Summary by API key"],
  "usage.236587078d": ["日期 · UTC", "Date · UTC"],
  "usage.3301c1b9b0": ["已结算记录", "Settled records"],
  "usage.29eafc8384": ["每日用量与费用", "Daily usage and cost"],
  "usage.b7aa37b789": [
    "{p0} — {p1} · UTC · 已更新",
    "{p0} — {p1} · UTC · Updated",
  ],
  "usage.4a2dbdd8f8": [
    "用量更新失败；此前数据如有保留，仅供核对。",
    "Usage update failed. Retained previous data, if any, is for checking only.",
  ],
  "usage.46dc6923c9": [
    "请核对日期范围（最长 366 天）和 API Key，再更新用量。",
    "Check date range (up to 366 days) and API key, then update usage.",
  ],
  "usage_chart.21a123d8e5": [
    "上图为已结算的估算费用，下图为调用记录，刻度各自独立。未知费用保留缺口，可聚焦日期核对。",
    "Top: settled estimated costs. Bottom: call records. Each has its own scale. Unknown costs leave gaps. Focus a date to inspect.",
  ],
  "usage_chart.096c8639da": ["这段时间暂无调用记录", "No calls in this period"],
  "usage_chart.f6553e40fc": [
    "更换日期或 API Key 查看其他记录。",
    "Change the date range or API key to view other records.",
  ],
  "usage_chart.8548090e15": [
    "每日估算费用与调用记录，上下两图共用日期轴、各自独立刻度",
    "Daily estimated costs and calls share a date axis with separate scales",
  ],
  "usage_chart.196eb8e955": [
    "估算费用折线图与调用记录柱状图",
    "Estimated cost line chart and call count bar chart",
  ],
  "usage_chart.ab850df450": [
    "预留金额不计入费用折线。使用 Tab 键或指针查看每日数据，下方提供表格。",
    "Reservations are excluded from the cost line. Use Tab or a pointer to inspect daily values; a table is below.",
  ],
  "usage_chart.2301377c15": [
    "估算费用（USD，已结算）",
    "Estimated cost (USD, settled)",
  ],
  "usage_chart.1b01ba1880": ["调用记录（次）", "Call records"],
  "usage_chart.2c3cde46d3": [
    "{p0} · {p1} 次 · 估算 {p2} · 待确认 {p3}",
    "{p0} · {p1} calls · Estimated {p2} · Unconfirmed {p3}",
  ],
  "view_sections.ae0fbe4358": ["功能分区", "Feature sections"],
  "workspace_sections.dcf264eaf4": ["用量与费用", "Usage & cost"],
  "workspace_sections.68d619f5e2": ["制作任务", "Creation tasks"],
  "workspace_sections.8221d92556": ["准备文档", "Prepare document"],
  "workspace_sections.4ba0031dc6": [
    "提取与处理设置",
    "Extraction and processing settings",
  ],
  "workspace_sections.7320166720": ["提醒设置", "Reminder settings"],
  "workspace_sections.d87514d81b": ["组件与版本", "Components and versions"],
  "workspace_sections.0f993817a0": ["外观与阅读", "Appearance & reading"],
  "workspace_sections.ddf1979f61": ["登录与会话", "Login & session"],
  "workspace_sections.37375b1a2d": [
    "知识库接入任务与历史",
    "Vault attachment tasks and history",
  ],
  "workspace_sections.9614d85606": ["知识库列表", "Vault list"],
  "workspace_sections.bcfc2992d4": ["保存与处理", "Saving and processing"],
  "workspace_sections.61c459a33a": [
    "布局与外部改动",
    "Layout and external changes",
  ],
  "workspace_sections.e229703d0b": ["接入知识库", "Attach vault"],
  "common.home": ["首页", "Home"],
  "common.inbox": ["收件箱", "Inbox"],
  "common.vaults": ["知识库", "Knowledge bases"],
  "common.imports": ["资料导入", "Import"],
  "common.inventory": ["资料与版本", "Sources & versions"],
  "common.graph": ["知识关联", "Organize & connect"],
  "common.creation": ["文档制作", "Create documents"],
  "common.channels": ["消息入口", "Message channels"],
  "common.clients": ["Agent 与客户端", "Agents & clients"],
  "common.models": ["模型与查询", "Models & search"],
  "common.usage": ["执行与费用", "Tasks & usage"],
  "common.backend": ["后台与提醒", "Backend & reminders"],
  "common.backups": ["备份与恢复", "Backup & restore"],
  "common.settings": ["系统设置", "Settings"],
  "common.refresh": ["刷新", "Refresh"],
  "common.save": ["保存", "Save"],
  "common.cancel": ["取消", "Cancel"],
  "common.language": ["界面语言", "Interface language"],
  "common.locale_failure": [
    "未能保存语言偏好，请检查客户端存储后重试。",
    "Language preference could not be saved. Check client storage and try again.",
  ],
  "common.local_title": [
    "Fiber bundle · 本机管理",
    "Fiber bundle · Local workspace",
  ],
  "common.locale_search_aliases": [
    "搜索 笔记 全文 search query",
    "search notes full text query",
  ],
  "common.locale_import_aliases": ["import 文件 上传", "import files upload"],
  "common.locale_expert_aliases": ["专家 pdf", "expert pdf"],
  "common.locale_inbox_aliases": ["inbox 待办 审核", "inbox tasks review"],
  "graph.webgl_unavailable": [
    "WebGL 不可用，请使用笔记列表。",
    "WebGL unavailable. Use the note list.",
  ],
  "graph.layout_failed": [
    "布局失败，已保留静态图和笔记列表。",
    "Layout failed. Static graph and note list retained.",
  ],
  "graph.layout_invalid": [
    "布局结果不可用，已暂停。",
    "Layout output unavailable. Paused.",
  ],
  "graph.layout_unavailable": [
    "无法启动布局，已保留静态视图。",
    "Cannot start layout. Static view retained.",
  ],
};
