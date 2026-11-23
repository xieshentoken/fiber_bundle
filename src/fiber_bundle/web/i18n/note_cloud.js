export default {
  "note_cloud.title": ["笔记云图", "Note cloud"],
  "note_cloud.identity": ["查看身份", "View as"],
  "note_cloud.vault": ["知识库", "Vault"],
  "note_cloud.intro": [
    "让已有的笔记关系浮现。悬停查看标题与直接关联，点击保留选择。",
    "Explore the relationships already in your notes. Hover to preview a title and its direct connections; click to keep a selection.",
  ],
  "note_cloud.search": [
    "搜索标题、路径或待核对目标",
    "Search titles, paths or unresolved targets",
  ],
  "note_cloud.find": ["查找", "Find"],
  "note_cloud.refresh": ["刷新", "Refresh"],
  "note_cloud.fit": ["适应画面", "Fit to view"],
  "note_cloud.zoomIn": ["放大", "Zoom in"],
  "note_cloud.zoomOut": ["缩小", "Zoom out"],
  "note_cloud.clear": ["清除选择", "Clear selection"],
  "note_cloud.loading": [
    "正在读取当前知识库的索引快照…",
    "Reading the current vault’s index snapshot…",
  ],
  "note_cloud.noScope": [
    "当前没有获准查询的知识库。",
    "No vault is authorized for this identity.",
  ],
  "note_cloud.empty": [
    "当前索引中没有可显示的笔记。请在知识库页面核对索引状态。",
    "No notes are available in the current index. Check index status on the Vaults page.",
  ],
  "note_cloud.failed": [
    "云图未能读取：{message}",
    "Could not read the note cloud: {message}",
  ],
  "note_cloud.fallback": [
    "图形渲染不可用，可继续使用下方可搜索的笔记列表。",
    "The graph renderer is unavailable. Use the searchable note list below.",
  ],
  "note_cloud.count": [
    "现存笔记 {notes} · 专家入口 {experts} · 待核对目标 {missing} · 显示关系 {edges}",
    "{notes} current notes · {experts} expert entries · {missing} unresolved targets · {edges} relationships shown",
  ],
  "note_cloud.coverage": [
    "显示当前可用索引，可能不包含尚未索引或已变化的笔记。",
    "This view uses the available index. Unindexed or changed notes may be missing.",
  ],
  "note_cloud.partial": [
    "这是有界局部视图：另有 {nodes} 个节点、{edges} 条边未显示。可搜索定位其他索引笔记。",
    "Bounded view: {nodes} more nodes and {edges} edges are not displayed. Search to locate other indexed notes.",
  ],
  "note_cloud.incomplete": [
    "部分当前来源或归属记录未纳入，当前视图未完整。",
    "Some current sources or membership records are not included; this view is partial.",
  ],
  "note_cloud.indexState": ["索引状态：{state}", "Index status: {state}"],
  "note_cloud.legend": [
    "实点：笔记 · 菱形：专家入口 · 空心：待核对目标。关系来自索引显式链接或已保存 MOC 归属；颜色与位置仅表示布局。",
    "Dots: notes · Diamonds: expert entries · Hollow circles: unresolved targets. Edges are indexed explicit links or saved MOC memberships; colors and positions represent layout only.",
  ],
  "note_cloud.hint": [
    "悬停预览 · 点击固定 · 拖动平移 · 滚轮缩放 · Esc 清除",
    "Hover to preview · Click to select · Drag to pan · Scroll to zoom · Esc to clear",
  ],
  "note_cloud.pick": [
    "从云图或笔记列表选择一篇笔记。",
    "Select a note in the cloud or the note list.",
  ],
  "note_cloud.related": ["直接关联（{count}）", "Direct connections ({count})"],
  "note_cloud.noRelated": [
    "当前快照中没有直接关联。",
    "No direct connections in this snapshot.",
  ],
  "note_cloud.explicit": ["显式链接", "Explicit link"],
  "note_cloud.moc": ["MOC 成员", "MOC member"],
  "note_cloud.open": ["打开原笔记", "Open original note"],
  "note_cloud.pathOnly": [
    "此笔记未登记稳定身份。可按上述相对路径在知识库中打开。",
    "This note has no registered stable identity. Open it in your vault using the relative path above.",
  ],
  "note_cloud.browserPath": [
    "请按上述相对路径在知识库中打开原笔记。",
    "Open the original note in your vault using the relative path above.",
  ],
  "note_cloud.openFailed": [
    "原笔记未打开：{message}",
    "Could not open the note: {message}",
  ],
  "note_cloud.list": [
    "笔记列表与键盘导航",
    "Note list and keyboard navigation",
  ],
  "note_cloud.noMatches": [
    "当前授权范围没有匹配实体。",
    "No matching entities in the authorized scope.",
  ],
  "note_cloud.resultCount": [
    "匹配 {count} 项{suffix}",
    "{count} matching items{suffix}",
  ],
  "note_cloud.truncated": [
    "（仅显示前 50 篇，请缩小关键词）",
    " (first 50 shown; refine your search)",
  ],
  "note_cloud.previous": ["上一页", "Previous"],
  "note_cloud.next": ["下一页", "Next"],
  "note_cloud.page": ["第 {page} / {pages} 页", "Page {page} / {pages}"],
  "note_cloud.selected": ["已选择：{title}", "Selected: {title}"],
  "note_cloud.searchFailed": [
    "搜索未完成：{message}",
    "Search did not finish: {message}",
  ],
  "note_cloud.excludedExperts": [
    "另有 {count} 个专家包入口未纳入此笔记视图。",
    "{count} expert package entries are not included in this note view.",
  ],
  "note_cloud.membershipMissing": [
    "另有 {count} 条 MOC 成员关系尚不可核对。",
    "{count} MOC memberships could not be checked.",
  ],
  "note_cloud.nav": ["笔记云图", "Note cloud"],
  "note_cloud.summary": [
    "从当前可用索引查看笔记、显式链接与已保存的 MOC 关系。",
    "Explore notes, explicit links and saved MOC relationships from the available index.",
  ],
  "note_cloud.defaults": ["默认范围设置", "Default scope settings"],
  "note_cloud.membershipCounts": [
    "已保存 MOC 归属 {count} · 人工 {manual} / 系统 {system} · 人工锁定 {locked}",
    "{count} saved MOC memberships · Manual {manual} / system {system} · {locked} pinned",
  ],
  "note_cloud.unresolvedCounts": [
    "{count} 个目标的当前文件待核对，历史归属保留；不计入现存笔记。",
    "Current files for {count} targets need checking. Saved memberships are retained; these targets are not counted as current notes.",
  ],
  "note_cloud.syncCounts": [
    "归属同步证据：索引有 {present} · 尚未同步 {unsynced} · 待核对 {unknown}",
    "Membership sync evidence: {present} in the index · {unsynced} not synced · {unknown} unknown",
  ],
  "note_cloud.kind_note": ["笔记", "Note"],
  "note_cloud.kind_moc": ["MOC", "MOC"],
  "note_cloud.kind_expert": ["专家入口", "Expert entry"],
  "note_cloud.kind_unresolved": ["待核对目标", "Unresolved target"],
  "note_cloud.unknownTarget": ["当前不可用目标", "Unavailable target"],
  "note_cloud.expertEntry": [
    "专家包入口 · {kind}",
    "Expert package entry · {kind}",
  ],
  "note_cloud.entry_directory": ["目录", "Directory"],
  "note_cloud.entry_markdown": ["Markdown", "Markdown"],
  "note_cloud.expertReview": [
    "产物 {stage} · 审核 {review} · {verified}",
    "Artifact: {stage} · Review: {review} · {verified}",
  ],
  "note_cloud.verified": ["知识已核验", "Knowledge verified"],
  "note_cloud.unverified": ["知识尚未核验", "Knowledge unverified"],
  "note_cloud.missingTarget": [
    "当前文件未找到。此节点不是现存笔记。",
    "Current file not found. This target is not a current note.",
  ],
  "note_cloud.unavailableTarget": [
    "当前目标无法安全核对。此节点不是现存笔记。",
    "This target could not be safely checked. It is not a current note.",
  ],
  "note_cloud.historicalPath": [
    "历史登记路径：{path}",
    "Historical registered path: {path}",
  ],
  "note_cloud.retainedMembership": [
    "保留已有 MOC 归属，不按标题推断新的文件对应关系。",
    "Existing MOC memberships are retained. No new file match is inferred from its title.",
  ],
  "note_cloud.savedMembership": ["已保存 MOC 归属", "Saved MOC membership"],
  "note_cloud.owner_manual": ["人工", "Manual"],
  "note_cloud.owner_system": ["系统", "System"],
  "note_cloud.locked": ["人工锁定", "Pinned by hand"],
  "note_cloud.notLocked": ["未锁定", "Not pinned"],
  "note_cloud.sync_indexed_present": [
    "索引中有同步证据",
    "Sync evidence exists in the index",
  ],
  "note_cloud.sync_not_synced": [
    "归属已保存，尚未同步到 MOC 文件",
    "Membership saved; not synced to the MOC file",
  ],
  "note_cloud.sync_unknown": [
    "文件同步状态待核对",
    "File sync state is unknown",
  ],
  "note_cloud.identityNotIndexed": [
    "当前索引未找到此身份。此目标不是已核对的现存笔记。",
    "This identity was not found in the current index. It is not a verified current note.",
  ],
  "note_cloud.evidence_index_not_current": [
    "依据：当前索引代次尚不可核对。",
    "Evidence: the current index generation could not be checked.",
  ],
  "note_cloud.evidence_indexed_moc_unavailable": [
    "依据：MOC 索引正文不可用或不完整。",
    "Evidence: indexed MOC text is unavailable or incomplete.",
  ],
  "note_cloud.evidence_pending_moc_file_plan": [
    "依据：存在未决的 MOC 文件操作。",
    "Evidence: a MOC file operation is pending.",
  ],
  "note_cloud.evidence_indexed_file_differs_from_applied": [
    "依据：索引文件与最近已应用计划不一致。",
    "Evidence: the indexed file differs from the latest applied plan.",
  ],
  "note_cloud.evidence_indexed_managed_identity": [
    "依据：受管块身份及已应用文件摘要相符。",
    "Evidence: the managed identity and applied file digest match.",
  ],
  "note_cloud.evidence_indexed_note_link": [
    "依据：真实链接及已应用文件摘要相符。",
    "Evidence: a real link and the applied file digest match.",
  ],
  "note_cloud.evidence_ledger_newer_than_indexed_applied_file": [
    "依据：归属记录更新，已应用文件尚未包含该成员。",
    "Evidence: the membership is newer; the applied file does not include this member.",
  ],
  "note_cloud.evidence_indexed_evidence_absent": [
    "依据：未找到可信的受管身份或真实链接。",
    "Evidence: no trusted managed identity or real link was found.",
  ],
  "note_cloud.evidence_unavailable": [
    "依据尚不可核对。",
    "Evidence could not be checked.",
  ],
  "note_cloud.artifactDraft": ["Draft", "Draft"],
  "note_cloud.artifactPublished": ["已发布", "Published"],
  "note_cloud.artifactFinal": ["完成稿", "Final"],
  "note_cloud.reviewDeferred": ["待审阅（已延后）", "Deferred review"],
  "note_cloud.reviewPending": ["待审阅", "Pending review"],
  "note_cloud.reviewReviewed": ["已审阅", "Reviewed"],
  "note_cloud.reviewApproved": ["已批准", "Approved"],
  "note_cloud.reviewRejected": ["已拒绝", "Rejected"],
  "note_cloud.publicationMissingConfirmed": [
    "原发布路径已标记缺失（历史记录）。此目标不是现存笔记。",
    "The original publication path was marked missing (historical record). This target is not a current note.",
  ],
};
