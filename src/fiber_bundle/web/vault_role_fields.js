import { setText, uiText } from "./i18n/index.js";
// Explicit v2 roles; v1 values never acquire new defaults on save.
import { el } from "./ui.js";
import { control } from "./record_ui.js";

export function createRoleFields(form, directoryField, state, primary) {
  const version = el("select"),
    label = el("label", uiText("目录规则版本"));
  for (const [value, text] of [
    ["1", "兼容现有规则"],
    ["2", "统一知识库目录"],
  ]) {
    const option = el("option", uiText(text));
    option.value = value;
    version.append(option);
  }
  version.value = String(state.policy_version || 1);
  label.append(version);
  form.append(label);
  const fields = Object.fromEntries(
    [
      ["moc_dir", "MOC 默认目录", "Map"],
      ["book_dir", "完整专家 Draft 目录", "Book"],
      ["attachment_root", "附件根目录（不索引正文）", "Attachments"],
      ["original_dir", "非图片原件与附件目录", "Attachments/pristin"],
    ].map(([key, text, fallback]) => [
      key,
      directoryField(text, state[key] || fallback),
    ]),
  );
  const mode = el("select"),
    modeLabel = el("label", uiText("确认 MOC 后归档方式"));
  for (const [value, text] of [
    ["off", "关闭"],
    ["preview", "仅准备预览"],
    ["automatic", "自动执行（普通笔记）"],
  ]) {
    const option = el("option", uiText(text));
    option.value = value;
    mode.append(option);
  }
  mode.value = state.moc_archive_mode || "off";
  const modeHelp = el("small", null, "muted");
  modeHelp.id = "vault-moc-archive-help";
  setText(mode, uiText("确认 MOC 后归档方式"), "aria-label");
  for (const text of [
    "关闭：仅建立 MOC 关联，不移动笔记。",
    "仅准备预览：生成归档预览，确认后再移动。",
    "自动执行（普通笔记）：确认关联后，自动归档符合条件的普通笔记。",
    "归档优先使用 MOC 对应目录，否则使用“已整理默认目录”；专家包沿用 Book 规则。",
  ]) {
    if (modeHelp.childNodes.length) modeHelp.append(el("br"));
    modeHelp.append(el("span", uiText(text)));
  }
  mode.setAttribute("aria-describedby", modeHelp.id);
  modeLabel.append(mode, modeHelp);
  form.append(modeLabel);
  const update = () => {
    primary.legacyArchive.disabled = version.value === "2";
    primary.legacyArchiveLabel.hidden = version.value === "2";
    modeHelp.hidden = version.value !== "2";
    for (const field of Object.values(fields))
      field.input.disabled = field.select.disabled = version.value !== "2";
    mode.disabled = version.value !== "2";
  };
  version.addEventListener("change", update);
  update();
  const preset = control("填入统一库目录预设", () => {
    version.value = "2";
    primary.mode.value = "obsidian_folders";
    primary.inbox.input.value = "Inbox";
    primary.archive.input.value = "Resource";
    primary.image.input.value = "Attachments/images";
    for (const [key, value] of Object.entries({
      moc_dir: "Map",
      book_dir: "Book",
      attachment_root: "Attachments",
      original_dir: "Attachments/pristin",
    }))
      fields[key].input.value = value;
    mode.value = "off";
    update();
    form.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
  preset.style.alignSelf = "start";
  form.append(preset);
  return {
    read: () =>
      version.value === "2"
        ? {
            policy_version: 2,
            moc_archive_mode: mode.value,
            ...Object.fromEntries(
              Object.entries(fields).map(([key, field]) => [
                key,
                field.input.value,
              ]),
            ),
          }
        : {},
    isUnified: () => version.value === "2",
  };
}
