import { phrase, setText, uiText } from "./i18n/index.js";
// Physical identity repair is a previewed metadata action, independent of archiving.
import { el } from "./ui.js";
import { control, showJSON } from "./record_ui.js";

export function renderRebind(api, host, current, scope, valid, refresh) {
  if (!current.locations?.length) return;
  const details = el("details"),
    select = el("select"),
    input = el("input"),
    preview = el("div");
  details.append(el("summary", uiText("外部移动或替换后重新绑定位置")));
  details.append(
    el(
      "p",
      uiText(
        "只更新文件位置和身份记录，保留来源与 MOC；不修改正文。外部移动后的链接仍需核对。",
      ),
    ),
  );
  setText(select, uiText("需要重新绑定的发布内容"), "aria-label");
  setText(input, uiText("重新绑定的入口相对路径"), "aria-label");
  input.maxLength = 1024;
  for (const [index, location] of current.locations.entries()) {
    const option = el(
      "option",
      uiText(
        (location.state === "missing" ? "[已标记缺失] " : "") +
          location.entry_note_path,
      ),
    );
    option.value = String(index);
    select.append(option);
  }
  let missingIntent = null;
  const missingConfirm = control("确认标记缺失（保留历史）", async () => {
    if (!missingIntent) return;
    await api.missingApply(missingIntent.operation_id, scope);
    if (valid()) await refresh();
  });
  missingConfirm.disabled = true;
  let pending = null,
    generation = 0;
  const confirm = control("确认重新绑定", async () => {
    if (!pending) return;
    await api.rebindAction(pending.operation_id, "apply", scope);
    if (valid()) await refresh();
  });
  function clear() {
    generation++;
    pending = null;
    missingIntent = null;
    missingConfirm.disabled = true;
    preview.replaceChildren();
    confirm.disabled = true;
    confirm.dataset.policyDisabled = "true";
  }
  select.addEventListener("change", () => {
    input.value = current.locations[Number(select.value)].entry_note_path;
    clear();
  });
  input.addEventListener("input", clear);
  input.value = current.locations[0].entry_note_path;
  clear();
  const prepare = control("预览重新绑定", async () => {
    const location = current.locations[Number(select.value)],
      ticket = ++generation;
    const packet = await api.rebindPreview({
      ...scope,
      kind: location.kind,
      publication_id: location.publication_id,
      expected_revision: location.revision,
      entry_note_path: input.value,
    });
    if (!valid() || ticket !== generation) return;
    pending = packet;
    preview.replaceChildren(
      el("p", `${packet.source_path} → ${packet.target_path}`),
    );
    preview.append(
      el("p", phrase("当前正文摘要：{p0}", { p0: packet.entry_sha256 })),
    );
    if (packet.modified_notes.length)
      preview.append(
        el(
          "p",
          phrase("保留人工改动：{p0}", {
            p0: packet.modified_notes.join("、"),
          }),
        ),
      );
    showJSON(preview, packet.file_mapping, "重绑文件位置映射");
    confirm.disabled = false;
    confirm.dataset.policyDisabled = "false";
  });
  const missingPrepare = control("预览标记文件缺失", async () => {
    clear();
    const location = current.locations[Number(select.value)],
      ticket = generation;
    if (location.state === "missing")
      throw new Error("此记录已标记为缺失。文件找回后可重新绑定位置。");
    const packet = await api.missingPreview({
      ...scope,
      kind: location.kind,
      publication_id: location.publication_id,
      expected_revision: location.revision,
    });
    if (!valid() || generation !== ticket) return;
    missingIntent = packet;
    preview.replaceChildren(
      el(
        "p",
        uiText(
          "将保留历史、来源和去重，仅将以下不存在的正文标记为缺失；不恢复或删除文件：",
        ),
      ),
      ...packet.missing_paths.map((p) => el("p", p)),
    );
    missingConfirm.disabled = false;
  });
  details.append(
    select,
    input,
    prepare,
    missingPrepare,
    preview,
    confirm,
    missingConfirm,
  );
  host.append(details);
}
