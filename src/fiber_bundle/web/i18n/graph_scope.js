// Shared classic/Atlas page-scope preferences and read feedback.
export default {
  "graph_scope.switch_failed": [
    "无法完成切换确认。当前范围与草稿已保留，请重新选择后再试。",
    "The switch confirmation could not be completed. The current scope and drafts are preserved. Select the target again to retry.",
  ],
  "graph_scope.switch_unavailable": [
    "当前范围尚未核对，不能放弃现有草稿并切换。请先保留草稿并核对当前读取错误。",
    "The current scope is not verified, so existing drafts cannot be discarded for a switch. Preserve the drafts and review the current read error first.",
  ],
  "graph_scope.organization_failed": [
    "整理目录读取未完成：{p0}。可使用页面刷新重试；如为索引问题，按下方现有索引入口处理。",
    "The organization catalog could not be read: {p0}. Retry with the page refresh control. For index issues, use the existing index controls below.",
  ],
  "graph_scope.switch_title": [
    "切换知识库前保留或放弃草稿",
    "Keep or discard drafts before switching vaults",
  ],
  "graph_scope.switch_body": [
    "当前范围：{p0}。目标范围：{p1}。切换会清除本页未保存内容及 MOC 预览；已提交任务和已完成结果保留。取消将保留当前范围与草稿。",
    "Current scope: {p0}. Target scope: {p1}. Switching clears unsaved page content and MOC previews. Submitted tasks and completed results are preserved. Cancel to keep the current scope and drafts.",
  ],
  "graph_scope.switch_accept": ["放弃草稿并切换", "Discard drafts and switch"],
  "graph_scope.switch_cancel": ["保留草稿，不切换", "Keep drafts; stay here"],
  "graph_scope.default_unavailable": [
    "页面默认：已不可用或无查询权限；保存值仍保留。",
    "Page default: unavailable or no longer queryable. The saved value is preserved.",
  ],
  "graph_scope.partial": [
    "{p0} 部分读取未完成：{p1}。请查看具体错误并使用现有入口重试或核对索引。",
    "Part of {p0} could not be read: {p1}. Check the reported error and use the existing retry or index controls.",
  ],
  "graph_scope.save": ["设为页面默认", "Set as page default"],
  "graph_scope.clear": ["清除默认", "Clear default"],
  "graph_scope.local_only": [
    "默认范围适用于当前浏览器或桌面客户端；不会更改访问权限或查询默认范围。临时切换不会改写默认。",
    "This default applies to the current browser or desktop client. It does not change permissions or default query scope. Temporary selections do not change it.",
  ],
  "graph_scope.default": ["页面默认：{p0}", "Page default: {p0}"],
  "graph_scope.no_default": [
    "页面默认：未设置；进入页面时选择首个可查询范围。",
    "Page default: not set. Opening this page selects the first queryable scope.",
  ],
  "graph_scope.invalid_default": [
    "已保存的页面默认已不可用或无查询权限；保留原默认，改用当前可查询范围。",
    "The saved page default is unavailable or no longer queryable. The saved value is preserved; an available scope is selected instead.",
  ],
  "graph_scope.invalid_current": [
    "当前范围已不可用或无查询权限，已切换到可查询范围；页面默认保持。",
    "The current scope is unavailable or no longer queryable. An available scope is selected; the page default is unchanged.",
  ],
  "graph_scope.no_scope": [
    "当前身份列表没有可查询知识库；已清除旧范围内容。",
    "No queryable vault is available for the current identities. Previous scope content has been cleared.",
  ],
  "graph_scope.storage_unavailable": [
    "无法读取本客户端的页面默认；仍可选择知识库并读取。",
    "The page default cannot be read on this client. You can still select a vault and load it.",
  ],
  "graph_scope.storage_corrupt": [
    "本客户端保存的页面默认格式无效；仍可读取，可重新保存或清除默认。",
    "The saved page default has an invalid format. Reading is still available; save a new default or clear it.",
  ],
  "graph_scope.saved": [
    "页面默认已保存；下次进入或重载时使用，当前资料不会重新扫描。",
    "Page default saved. It will be used on the next visit or reload. Current material is not scanned again.",
  ],
  "graph_scope.cleared": [
    "页面默认已清除；当前选择保留，下次进入使用首个可查询范围。",
    "Page default cleared. The current selection is preserved; the next visit uses the first queryable scope.",
  ],
  "graph_scope.save_failed": [
    "页面默认保存失败，之前的默认保持；请检查本客户端是否允许存储。",
    "The page default could not be saved. The previous default is preserved. Check whether storage is allowed on this client.",
  ],
  "graph_scope.clear_failed": [
    "页面默认清除失败，之前的默认保持；请检查本客户端是否允许存储。",
    "The page default could not be cleared. The previous default is preserved. Check whether storage is allowed on this client.",
  ],
  "graph_scope.loading_catalog": [
    "正在读取当前身份与可查询知识库…",
    "Reading current identities and queryable vaults…",
  ],
  "graph_scope.loading": [
    "正在读取 {p0} 的目录、MOC、任务与现有索引状态…",
    "Reading directories, MOCs, tasks, and existing index status for {p0}…",
  ],
  "graph_scope.ready": [
    "当前范围：{p0}；本次读取已返回，请查看各面板状态。",
    "Current scope: {p0}. This read has returned; check each panel’s status.",
  ],
  "graph_scope.failed": [
    "{p0} 读取未完成：{p1}。可使用页面刷新重试；索引问题请按现有索引入口处理。",
    "Reading {p0} did not complete: {p1}. Retry with the page refresh control. For index issues, use the existing index controls.",
  ],
  "graph_scope.busy": [
    "当前操作正在提交，请等待结果后再刷新或切换范围。",
    "An operation is being submitted. Wait for its result before refreshing or switching scope.",
  ],
};
