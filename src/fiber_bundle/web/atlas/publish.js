// A reviewed candidate gets a concrete path confirmation before any vault write.
import { confirm } from "./dialog.js";
import { strings } from "./i18n.js";

const t = strings({
  zh: {
    title: "确认发布位置",
    body: "确认将已审核内容保存到以下位置？",
    entry: "正文",
    package: "完整内容包",
    other: "其他文件 {n} 项",
    more: "其余文件保存在同一已确认布局中。",
    confirm: "保存到这些位置",
  },
  en: {
    title: "Confirm publication location",
    body: "Save the reviewed content to these locations?",
    entry: "Main note",
    package: "Full package",
    other: "{n} other files",
    more: "Remaining files follow the same confirmed layout.",
    confirm: "Save to these locations",
  },
});
export async function confirmPublication(api, kind, identity, body = {}) {
  const packet = await api.previewPublication(kind, identity, body);
  if (packet.state === "published") return true;
  const paths = packet.paths || [packet.entry_note_path];
  const entry = packet.entry_note_path;
  const other = paths.filter((path) => path !== entry);
  const details = [t("body"), { label: t("entry"), items: [entry] }];
  if (packet.package_root)
    details.push({ label: t("package"), items: [packet.package_root] });
  if (other.length)
    details.push({
      label: t("other", { n: other.length }),
      items: other.slice(0, 8),
    });
  if (other.length > 8) details.push(t("more"));
  const confirmed = await confirm({
    title: t("title"),
    body: details,
    confirmLabel: t("confirm"),
  });
  if (!confirmed)
    await api.discardPublicationPreview(kind, packet.publication_id);
  return confirmed;
}
