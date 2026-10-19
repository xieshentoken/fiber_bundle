import { phrase, setText, uiText } from "./i18n/index.js";
// External edits are reconciled by explicit selection, preview, and durable confirmation.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { vaultPlacementErrorText } from "./vault_placement_api.js";

const states = {
  moved: "发现候选位置",
  replaced: "原路径文件已替换",
  ambiguous: "存在多个候选",
  unlocated: "未定位，需核对",
  partial: "资料包部分正文仍在",
};

export function renderExternalChanges(api, host, scope, valid) {
  const section = el("details"),
    message = el("p"),
    content = el("div"),
    history = el("div");
  section.append(
    el("summary", uiText("批量核对外部改动")),
    el(
      "p",
      uiText(
        "扫描已登记的普通笔记、Map及已发布资料的移动、改名和替换。未定位不等于删除；默认暂不处理。只更新位置、文件身份或缺失状态，保留正文、附件、来源、历史、去重和人工 MOC 关系，不重新入库。",
      ),
    ),
    message,
  );
  message.setAttribute("role", "status");
  let revision = 0,
    busy = false,
    invalidatePreview = () => {};
  async function run(work) {
    if (busy || !valid()) return;
    busy = true;
    content.inert = history.inert = true;
    try {
      await work();
    } catch (error) {
      if (valid()) setText(message, uiText(error.message));
    } finally {
      busy = false;
      content.inert = history.inert = false;
    }
  }
  function showBatch(packet, preview) {
    preview.replaceChildren();
    preview.append(
      el(
        "h4",
        packet.state === "completed"
          ? uiText("本批核对完成")
          : packet.state === "partial"
            ? uiText("部分完成：已完成项不会重复执行")
            : uiText("本批变更预览"),
      ),
    );
    for (const item of packet.items) {
      const row = el("div", null, "record-card");
      row.style.overflowWrap = "anywhere";
      row.append(
        el(
          "p",
          uiText(
            item.source_path +
              " → " +
              (item.target_path || "标记缺失（保留历史）"),
          ),
        ),
        el(
          "p",
          item.state === "completed"
            ? uiText("已完成")
            : item.state === "failed"
              ? uiText("未完成")
              : uiText("待确认"),
        ),
      );
      if (item.modified_notes?.length)
        row.append(el("p", uiText("将保留现有正文的人工修改。")));
      if (item.error)
        row.append(
          el(
            "p",
            uiText(
              vaultPlacementErrorText(item.error) ||
                "本项未完成，请重新扫描并核对。",
            ),
            "error",
          ),
        );
      preview.append(row);
    }
    preview.append(
      el(
        "p",
        uiText(
          "不改写笔记或 Map 正文；外部移动后的旧文本链接仍需核对。完成后请更新此知识库索引。",
        ),
      ),
    );
    if (packet.state === "completed") return;
    const ack = el("input"),
      label = el(
        "label",
        uiText("我已核对位置映射，并确认所选缺失项确实已删除。"),
      ),
      confirm = control(
        packet.state === "prepared" ? "确认执行本批核对" : "恢复本批未完成项",
        () =>
          run(async () => {
            if (!ack.checked || !valid()) return;
            setText(
              message,
              uiText(
                "正在逐项更新位置与缺失状态；请等待结果，未知结果时先读取批次状态。",
              ),
            );
            let value,
              completed = packet.items.filter(
                (item) => item.state === "completed",
              ).length;
            do {
              value = await api.externalApply(packet.operation_id, scope);
              if (!valid()) return;
              const next = value.items.filter(
                (item) => item.state === "completed",
              ).length;
              if (value.state === "running" && next <= completed)
                throw new Error(
                  "本批进度未推进，请先读取批次状态，不要重新入库。",
                );
              completed = next;
              setText(
                message,
                phrase("正在核对：已完成 {p0}/{p1} 项。", {
                  p0: completed,
                  p1: value.items.length,
                }),
              );
            } while (value.state === "running");
            setText(
              message,
              value.state === "completed"
                ? uiText("核对完成，请更新此知识库索引。")
                : uiText("部分完成，请查看各项原因。"),
            );
            showBatch(value, preview);
          }),
      );
    ack.type = "checkbox";
    confirm.disabled = true;
    confirm.dataset.policyDisabled = "true";
    ack.addEventListener("change", () => {
      confirm.disabled = !ack.checked;
      confirm.dataset.policyDisabled = String(!ack.checked);
    });
    label.prepend(ack);
    preview.append(
      label,
      confirm,
      control("重新读取本批状态", () =>
        run(async () => {
          const value = await api.externalRead(
            packet.operation_id,
            scope.client_id,
            scope.vault_id,
          );
          if (valid()) showBatch(value, preview);
        }),
      ),
    );
  }
  section.append(
    control("扫描外部改动", () =>
      run(async () => {
        setText(
          message,
          uiText("正在核对允许范围内的文件，不会修改资料或调用模型…"),
        );
        const value = await api.scanExternal(scope);
        if (!valid()) return;
        revision++;
        content.replaceChildren();
        history.replaceChildren();
        setText(
          message,
          phrase("{p0} 项需要核对；扫描未写入笔记或分类。", {
            p0: value.items.length,
          }),
        );
        for (const text of value.warnings)
          content.append(el("p", uiText(text), "muted"));
        const inputs = [],
          preview = el("div");
        const ordinary = value.items.filter((item) => item.kind === "note");
        if (ordinary.length)
          content.append(
            el(
              "p",
              phrase(
                "{p0} 项普通笔记文件身份变化会阻止索引；另 {p1} 项可同步路径登记。均保留原笔记 ID 和人工 MOC 关系。",
                {
                  p0: ordinary.filter((item) => item.blocks_index).length,
                  p1: ordinary.filter((item) => !item.blocks_index).length,
                },
              ),
            ),
          );
        invalidatePreview = () => {
          revision++;
          preview.replaceChildren();
        };
        for (const item of value.items) {
          const row = el("div", null, "record-card"),
            select = el("select"),
            target = el("input");
          row.style.overflowWrap = "anywhere";
          row.append(
            el("h4", item.source_path),
            el("p", states[item.state] || item.state),
          );
          setText(
            select,
            phrase("处理方式 {p0}", { p0: item.source_path }),
            "aria-label",
          );
          for (const [key, name] of [
            ["skip", "暂不处理"],
            ["rebind", "重新绑定当前位置"],
            ["missing", "确认已删除，标记缺失"],
          ]) {
            const option = el("option", uiText(name));
            option.value = key;
            option.disabled = key === "missing" && !item.can_mark_missing;
            select.append(option);
          }
          setText(
            target,
            phrase("当前位置 {p0}", { p0: item.source_path }),
            "aria-label",
          );
          setText(target, uiText("填写当前 Markdown 相对路径"), "placeholder");
          target.value = item.suggested_path || "";
          target.maxLength = 1024;
          target.disabled = true;
          select.addEventListener("change", () => {
            target.disabled = select.value !== "rebind";
            invalidatePreview();
          });
          target.addEventListener("input", invalidatePreview);
          row.append(select, target);
          for (const candidate of item.candidates)
            row.append(
              el(
                "p",
                phrase("候选：{p0}（{p1}）", {
                  p0: candidate.path,
                  p1:
                    {
                      file_identity: "文件身份一致",
                      content_hash: "正文摘要一致",
                      original_path: "原路径现有文件",
                    }[candidate.evidence] || "需核对",
                }),
              ),
            );
          inputs.push({ item, select, target });
          content.append(row);
        }
        if (inputs.length)
          content.prepend(
            control("采用所有唯一候选位置", () => {
              invalidatePreview();
              for (const { item, select, target } of inputs)
                if (item.suggested_path) {
                  select.value = "rebind";
                  target.value = item.suggested_path;
                  target.disabled = false;
                }
            }),
            control("将所有未定位项选为已删除", () => {
              invalidatePreview();
              for (const { item, select, target } of inputs)
                if (item.can_mark_missing) {
                  select.value = "missing";
                  target.disabled = true;
                }
              setText(
                message,
                uiText(
                  "已选中未定位项；请核实确实删除后再预览确认。不会删除任何文件。",
                ),
              );
            }),
          );
        if (inputs.length)
          content.append(
            control("生成批量核对预览", () =>
              run(async () => {
                const selected = inputs.filter(
                  ({ select }) => select.value !== "skip",
                );
                if (!selected.length)
                  throw new Error("请先选择需要重新绑定或确认删除的项目。");
                const ticket = revision,
                  packet = await api.externalPreview({
                    ...scope,
                    items: selected.map(({ item, select, target }) => ({
                      kind: item.kind,
                      publication_id: item.publication_id,
                      expected_revision: item.expected_revision,
                      action: select.value,
                      target_path:
                        select.value === "rebind" ? target.value.trim() : null,
                      deletion_confirmed: select.value === "missing",
                    })),
                  });
                if (valid() && ticket === revision) {
                  setText(
                    message,
                    uiText(
                      "预览待确认；尚未更新位置或缺失状态。预览有效期为10分钟。",
                    ),
                  );
                  showBatch(packet, preview);
                }
              }),
            ),
            preview,
          );
        for (const batch of value.batches)
          history.append(
            control("读取核对批次 · " + batch.state, () =>
              run(async () => {
                const packet = await api.externalRead(
                  batch.operation_id,
                  scope.client_id,
                  scope.vault_id,
                );
                if (valid()) showBatch(packet, preview);
              }),
            ),
          );
        if (!inputs.length) content.append(preview);
      }),
    ),
    content,
    history,
  );
  host.replaceChildren(section);
}
