// Task continuity and independent membership/archive progress.
export default {
  "workspace.preparing": [
    "正在准备新整理；此前结果保留在历史任务中。",
    "Preparing a new task; previous results remain in task history.",
  ],
  "continuity.0": [
    "等待原任务 {p0} · 已处理 {p1}/{p2} 项",
    "Waiting for task {p0} · {p1}/{p2} processed",
  ],
  "continuity.1": ["查看阻塞任务", "Show blocking task"],
  "continuity.2": [
    "结束任务并保留已完成结果…",
    "End task and keep completed results…",
  ],
  "continuity.3": [
    "无法核对任务结束范围，请刷新。",
    "Could not verify the closing scope. Refresh and retry.",
  ],
  "continuity.4": [
    "保留已处理 {p0} 项及已归档 {p1} 项，结束剩余 {p2} 项；不撤销文件，历史保留。",
    "Keep {p0} processed and {p1} archived items; end the remaining {p2} items. Files and history are retained.",
  ],
  "continuity.5": [
    "我已核对，保留结果并结束此任务。",
    "I reviewed the results and confirm ending this task without undoing them.",
  ],
  "continuity.6": ["确认结束并保留结果", "Confirm end and keep results"],
  "continuity.7": ["等待原任务", "Waiting for original task"],
  "continuity.8": ["等待原任务处理", "Waiting for the original task"],
  "continuity.9": ["已结束 · 保留结果", "Ended · results retained"],
  "continuity.10": [
    "已结束 · 已完成结果保留",
    "Ended · completed results retained",
  ],
  "continuity.11": ["等待文件就绪", "Waiting for files"],
  "continuity.12": [
    "部分已完成 · 等待文件就绪",
    "Partly complete · waiting for files",
  ],
  "continuity.13": [
    "只准备本任务等待中的文件；每次最多 10 篇，不自动重复下载。",
    "Prepare only files waiting in this task, up to 10 per click. Downloads are not automatically repeated.",
  ],
  "continuity.14": ["准备等待资料并继续", "Prepare waiting files and continue"],
  "continuity.15": ["已检查 {p0}/{p1} 篇", "Checked {p0}/{p1} notes"],
  "continuity.16": [
    "本次已就绪 {p0}/{p1} 篇；其余请稍后检查，原任务保持等待。",
    "{p0}/{p1} notes are ready. Check the others later; this task remains waiting.",
  ],
  "continuity.17": ["{p0}：{p1}", "{p0}: {p1}"],
  "continuity.18": ["已在本机", "Available locally"],
  "continuity.19": ["已请求下载", "Download requested"],
  "continuity.20": ["下载中", "Downloading"],
  "continuity.21": ["存在版本冲突", "Version conflict"],
  "continuity.22": ["检查失败，请核对文件", "Check failed; review the file"],
  "continuity.23": ["尚未请求下载", "Download not requested"],
  "continuity.24": [
    "分类：{p0} · MOC：{p1} · 归档：{p2}",
    "Classification: {p0} · MOC: {p1} · Archive: {p2}",
  ],
  "continuity.25": [
    "当前正文已变化，请保留已完成结果并按当前资料生成新预览。",
    "The current text changed. Keep completed results and generate a new preview from the current files.",
  ],
  "continuity.26": [
    "此项等待文件可读，其他无冲突条目可继续。",
    "This item is waiting for a readable file. Other items without conflicts can continue.",
  ],
  "continuity.27": [
    "已分析 {p0} · 当前已挂载 {p1} · 已归档 {p2} · 等待 {p3}",
    "Analyzed {p0} · Currently mounted {p1} · Archived {p2} · Waiting {p3}",
  ],
  "continuity.28": [
    "本模式只调整 MOC 归属，不移动文件。",
    "This mode updates MOC membership without moving files.",
  ],
  "continuity.29": [
    "此任务按已确认的快速整理预览归档；保存规则中的自动挂载后归档开关未启用。",
    "This task archives according to its confirmed quick-organize preview. Automatic archiving after mounting is disabled in the save rules.",
  ],
  "continuity.30": ["已挂载", "Mounted"],
  "continuity.31": ["尚未挂载", "Not mounted"],
  "continuity.32": ["归档延后", "Archive deferred"],
  "continuity.33": ["本模式不移动文件", "No moves in this mode"],
  "continuity.35": ["挂载待核对", "Membership needs verification"],
  "continuity.34": ["未计划归档", "No archive planned"],
  "archive.preview.summary": [
    "可挂载或保留归属 {p0} 项 · 所选范围可归档 {p1} 项 · 暂缓归档 {p2} 项",
    "Mount or retain membership: {p0} · Ready within selected scope: {p1} · Archive deferred: {p2}",
  ],
  "archive.preview.intro": [
    "暂缓项保留可靠的 MOC 挂载与原路径；其他无冲突条目继续。请展开条目核对文件、链接及行号。",
    "Deferred items keep their reliable MOC memberships and original paths. Independent items continue. Expand an item for the file, link and line.",
  ],
  "archive.preview.target": [
    "归档目标已存在；本项保留原路径与可靠挂载。",
    "The archive destination exists. This item keeps its original path and reliable membership.",
  ],
  "archive.preview.issue": [
    "本项链接需核对，暂缓归档；原文保留，可靠挂载可继续。",
    "This item's links need review. Archiving is deferred; its text is preserved and reliable mounting can continue.",
  ],
  "archive.preview.link": [
    "{p0} · 第 {p1} 行 · 链接：{p2}",
    "{p0} · Line {p1} · Link: {p2}",
  ],
  "archive.preview.line": ["未定位", "Not located"],
  "archive.recovery.inspect": [
    "核对并暂缓受阻归档…",
    "Review and defer blocked archives…",
  ],
  "archive.recovery.invalid": [
    "无法核对归档暂缓范围，请刷新。",
    "Could not verify the archive deferral. Refresh and retry.",
  ],
  "archive.recovery.proof": [
    "已核对 {p0} 项归档尚未移动或交换文件。保留挂载并暂缓这些移动，继续其余独立项。",
    "Verified that {p0} archives have not moved or exchanged files. Keep their memberships, defer these moves and continue independent items.",
  ],
  "archive.recovery.consent": [
    "我已核对，暂缓受阻移动并继续独立项。",
    "I reviewed the evidence and confirm deferring blocked moves and continuing independent items.",
  ],
  "archive.recovery.confirm": [
    "确认暂缓归档并继续",
    "Confirm deferral and continue",
  ],
  "archive.recovery.waiting": ["归档暂缓待核对", "Archive deferred for review"],
  "archive.recovery.phase": [
    "部分已完成 · 归档待核对",
    "Partly complete · archives need review",
  ],
  "archive.recovery.resume": [
    "原归档预览尚不可执行，直接恢复无法更新原计划。请先选择“核对并暂缓受阻归档”，或核对后结束任务并保留结果。",
    "The original archive is not executable. Resuming cannot update its frozen plan. Review and defer blocked archives, or review and end the task while keeping results.",
  ],
  "archive.recovery.edited": [
    "如需按修改后的正文归档，请先保留结果结束本任务，再按当前资料生成新预览。",
    "To archive using edited text, end this task while keeping results, then generate a new preview from the current files.",
  ],
  "archive.recovery.expired": [
    "原归档预览已过期；本项保留原路径与可靠挂载。",
    "The original archive preview expired. This item keeps its original path and reliable membership.",
  ],
  "archive.recovery.dependency": [
    "本次移动会改写暂缓项的链接，暂缓此依赖项；其他独立项继续。",
    "This move would rewrite a deferred source. Defer this dependent item; other independent items continue.",
  ],
  "archive.preview.coverage": [
    "预览已核对所选范围的正文与链接；外部来源仅检查文件版本，完整反向引用在归档前核对。",
    "The preview checks text and links in the selected scope and file revisions for external sources. Complete inbound-link validation runs before archiving.",
  ],
  "archive.dependency.failure": [
    "引用来源发生变化或暂未能核对；尚未确认执行。以下文件需要重新核对：",
    "Reference sources changed or could not be verified; execution is not confirmed. Review these files:",
  ],
  "archive.dependency.missing": [
    "来源文件已不在原路径",
    "Source file is missing at its original path",
  ],
  "archive.dependency.budget": [
    "变化来源超过本次有界核对范围",
    "Changed sources exceed this bounded check",
  ],
  "archive.dependency.unavailable": [
    "来源文件暂不可读",
    "Source file is temporarily unreadable",
  ],
  "archive.dependency.changing": [
    "文件在核对期间仍有变化",
    "Files changed during verification",
  ],
  "archive.dependency.backlink": [
    "归档依赖的引用已变化",
    "References required by this archive changed",
  ],
  "archive.dependency.inputs": ["归档依据已变化", "Archive inputs changed"],
  "archive.recovery.badge": ["归档待核对", "Archives need review"],
};
