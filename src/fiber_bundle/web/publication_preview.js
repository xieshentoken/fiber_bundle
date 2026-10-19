import { confirmAction } from "./confirm_dialog.js";
// A reviewed candidate gets a concrete path confirmation before any Vault write.
export async function confirmPublication(api, kind, identity, body = {}) {
  const packet = await api.previewPublication(kind, identity, body);
  if (packet.state === "published") return true;
  const paths = packet.paths || [packet.entry_note_path];
  const entry = packet.entry_note_path;
  const other = paths.filter((path) => path !== entry);
  const details = [
    "确认将已审核内容保存到以下位置？",
    { label: "正文", items: [entry] },
  ];
  if (packet.package_root)
    details.push({ label: "完整内容包", items: [packet.package_root] });
  if (other.length)
    details.push({
      label: `其他文件 ${other.length} 项`,
      items: other.slice(0, 8),
    });
  if (other.length > 8) details.push("其余文件保存在同一已确认布局中。");
  const confirmed = await confirmAction({
    title: "确认发布位置",
    body: details,
    confirmLabel: "保存到这些位置",
  });
  if (!confirmed)
    await api.discardPublicationPreview(kind, packet.publication_id);
  return confirmed;
}
