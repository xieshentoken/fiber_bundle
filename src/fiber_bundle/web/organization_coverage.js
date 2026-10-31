import { cloudRecoveryControls } from "./organization_cloud_recovery.js";
// Paths are source data, not UI messages. Coverage is never a write authorization.
import { el } from "./ui.js";
import { disclosure } from "./view_sections.js";
import { phrase, uiText, sourceText, joinText } from "./i18n/index.js";

export function coverageNotice(
  coverage,
  key = "organization-coverage",
  failed = false,
  recovery = null,
) {
  if (!coverage || coverage.complete) return null;
  const host = el("div", null, "task-impact");
  host.append(
    el(
      "p",
      failed
        ? uiText("以下资料暂不可用，本次操作尚未完成。")
        : phrase(
            coverage.historical
              ? "本次预览跳过 {p0} 项；这是预览时的记录，当前状态可单独检查。"
              : "当前可读资料已列出；{p0} 项暂未参与本次读取。",
            {
              p0: coverage.unavailable_count,
            },
          ),
    ),
  );
  const details = disclosure("查看暂不可用的资料");
  details.dataset.viewKey = key;
  for (const item of coverage.unavailable || []) {
    const reason =
      {
        vault_sync_cloud_pending:
          "本地内容尚不可读，可检查系统状态或主动下载此文件。",
        cloud_item_not_downloaded:
          "本地内容尚不可读，可检查系统状态或主动下载此文件。",
        vault_sync_source_changed: "内容已变化，等待索引更新。",
        vault_sync_pending: "内容已变化，等待索引更新。",
      }[item.reason] || "暂不可读，请核对文件或索引状态。";
    details.append(
      el("p", joinText([sourceText(item.path), " · ", uiText(reason)])),
    );
    if (
      recovery?.api?.cloudFile &&
      recovery.scope?.client_id &&
      recovery.scope?.vault_id
    )
      details.append(
        cloudRecoveryControls(item.path, recovery.scope, recovery.api),
      );
  }
  host.append(details);
  return host;
}
