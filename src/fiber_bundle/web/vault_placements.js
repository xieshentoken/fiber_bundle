import { joinText, phrase, setText, translate, uiText } from "./i18n/index.js";
// Explicit same-Vault organization and optional one-at-a-time historical conversion.
import { el, captureMessage } from "./ui.js";
import { control, showJSON } from "./record_ui.js";
import { renderMocRegistration } from "./vault_moc_registration.js";
import { renderRebind } from "./vault_rebind.js";
import { renderExternalChanges } from "./vault_external_changes.js";
import { revealSection } from "./view_sections.js";

export function createVaultPlacementView(api, host) {
  let generation = 0,
    intent = null,
    selectedVault = null;
  function clear() {
    generation++;
    intent = null;
    selectedVault = null;
    host.replaceChildren();
    host.hidden = true;
  }
  async function open(vault, clients) {
    revealSection(host);
    if (selectedVault === vault.id && !host.hidden) return;
    if (
      intent?.state === "prepared" &&
      !window.confirm(
        translate("切换会关闭当前预览，未确认的操作不会执行。是否继续？"),
      )
    )
      return;
    clear();
    selectedVault = vault.id;
    host.hidden = false;
    const epoch = generation,
      notify = captureMessage("vaults"),
      client = el("select"),
      content = el("div"),
      external = el("div");
    setText(client, uiText("整理使用的客户端权限"), "aria-label");
    const eligible = clients.filter(
      (c) =>
        c.active &&
        !c.managed_connection_id &&
        c.grants.some(
          (g) => g.vault_id === vault.id && g.can_query && g.can_add,
        ),
    );
    for (const c of eligible) {
      const option = el("option", c.name);
      option.value = c.id;
      client.append(option);
    }
    host.append(
      el("h3", phrase("{p0} · 整理与布局迁移", { p0: vault.name })),
      el(
        "p",
        uiText(
          "MOC 关联与归档分别记录。历史转换仅处理你逐项选定的已发布内容，并保留旧包及附件。",
        ),
      ),
      client,
      external,
      content,
      control("关闭整理", clear),
    );
    if (vault.read_only || !eligible.length) {
      content.append(
        el(
          "p",
          uiText("需要可查询且可新增的客户端，以及可写的仓库。"),
          "muted",
        ),
      );
      return;
    }
    let load = 0;
    function scope() {
      return { client_id: client.value, vault_id: vault.id };
    }
    let externalRevision = 0;
    function externalSection() {
      const ticket = ++externalRevision;
      const captured = scope();
      renderExternalChanges(
        api,
        external,
        captured,
        () =>
          epoch === generation &&
          ticket === externalRevision &&
          client.value === captured.client_id,
      );
    }
    externalSection();
    async function refresh() {
      const stamp = ++load,
        captured = scope();
      intent = null;
      content.replaceChildren(el("p", uiText("正在读取可整理笔记与操作记录…")));
      const current = await api.list(captured.client_id, captured.vault_id);
      if (epoch !== generation || stamp !== load) return;
      content.replaceChildren();
      async function mutate(work) {
        if (content.inert) throw new Error("当前操作尚未完成，请等待结果。");
        content.inert = true;
        content.setAttribute("aria-busy", "true");
        try {
          const result = await work();
          if (epoch === generation && stamp === load) await refresh();
          return result;
        } finally {
          content.inert = false;
          content.removeAttribute("aria-busy");
        }
      }
      if (current.automatic_archives?.length) {
        const section = el("section");
        section.append(el("h4", uiText("MOC 自动归档任务")));
        for (const task of current.automatic_archives) {
          const row = el("div", null, "record-card");
          row.append(
            el(
              "p",
              `${task.note_path} · ${task.state}${task.reason ? " · " + task.reason : ""}`,
            ),
          );
          if (
            [
              "queued",
              "needs_review",
              "needs_selection",
              "reconcile",
              "index",
            ].includes(task.state)
          ) {
            row.append(
              control("停止此归档（保留已确认关联）", async () => {
                await mutate(() =>
                  api.automaticAction(task.id, "cancel", captured),
                );
              }),
            );
            if (!["queued", "needs_selection"].includes(task.state))
              row.append(
                control("核对冻结版本并继续", async () => {
                  await mutate(() =>
                    api.automaticAction(task.id, "reconcile", captured),
                  );
                }),
              );
          }
          if (task.state === "needs_selection")
            row.append(
              el(
                "p",
                uiText("多个 MOC 对应不同目录，请在下方归档预览中明确选择。"),
              ),
            );
          section.append(row);
        }
        content.append(section);
      }
      for (const op of current.operations) {
        if (!["prepared", "applying", "reconcile"].includes(op.state)) continue;
        const card = el("div", null, "record-card"),
          migration = op.kind === "layout_migration",
          perform = migration
            ? api.migrationAction
            : op.kind === "publication_rebind"
              ? api.rebindAction
              : api.action;
        card.append(
          el(
            "p",
            phrase("待处理：{p0} · {p1}", {
              p0: migration
                ? uiText("历史布局转换")
                : op.kind === "publication_rebind"
                  ? uiText("位置重新绑定")
                  : uiText("归档或关联"),
              p1: uiText(op.state),
            }),
          ),
        );
        card.append(
          control(
            op.state === "prepared" ? "取消待确认操作" : "核对并恢复此操作",
            async () => {
              await mutate(() =>
                perform(
                  op.id,
                  op.state === "prepared" ? "cancel" : "reconcile",
                  captured,
                ),
              );
            },
          ),
        );
        if (op.preview) {
          card.append(
            el(
              "p",
              joinText([
                "",
                op.preview.source_path,
                " → ",
                op.preview.target_path || "仅关联",
                "",
              ]),
            ),
          );
          card.append(
            el(
              "p",
              phrase("关联状态：{p0}", { p0: op.preview.association_state }),
            ),
          );
          for (const issue of op.preview.unresolved_links)
            card.append(
              el(
                "p",
                phrase("待修复：{p0} → {p1}", {
                  p0: issue.note_path,
                  p1: issue.target,
                }),
                "error",
              ),
            );
          showJSON(card, op.preview.file_mapping, "待处理文件映射");
          if (op.state === "prepared" && op.preview.ready)
            card.append(
              control("确认执行以上待确认预览", async () => {
                await mutate(() => perform(op.id, "apply", captured));
              }),
            );
        }
        content.append(card);
      }
      renderMocRegistration(
        api,
        content,
        captured,
        () => epoch === generation && stamp === load,
        refresh,
      );
      const missing = current.locations.filter(
        (location) => location.state === "missing",
      );
      if (missing.length)
        content.append(
          el(
            "p",
            phrase(
              "{p0} 项历史发布已标记文件缺失，历史与去重保留；文件找回后可在下方重新绑定。",
              { p0: missing.length },
            ),
            "muted",
          ),
        );
      if (!current.blocked)
        renderRebind(
          {
            ...api,
            rebindAction: (...args) => mutate(() => api.rebindAction(...args)),
            missingApply: (...args) => mutate(() => api.missingApply(...args)),
          },
          content,
          current,
          captured,
          () => epoch === generation && stamp === load,
          async () => {},
        );
      if (!current.available) {
        content.append(
          el(
            "p",
            current.rebind_required
              ? uiText("笔记位置或文件身份已变化，请先使用上方入口重新绑定。")
              : current.blocked
                ? uiText(
                    "存在登记或归档待核对操作，请先恢复。关联可能已完成，文件保留在操作记录中。",
                  )
                : uiText("请先在保存规则中启用标题笔记布局。"),
            "muted",
          ),
        );
        return;
      }
      const notes = el("select"),
        mocs = el("select"),
        destination = el("select"),
        preview = el("div"),
        actions = el("div", null, "actions");
      setText(notes, uiText("待整理笔记"), "aria-label");
      setText(mocs, uiText("明确关联的 MOC"), "aria-label");
      setText(destination, uiText("冲突时选择归档目录"), "aria-label");
      destination.hidden = true;
      for (const n of current.notes) {
        const o = el("option", n.note_path);
        o.value = n.note_identity;
        notes.append(o);
      }
      const empty = el("option", uiText("选择明确关联的 MOC"));
      empty.value = "";
      mocs.append(empty);
      for (const m of current.mocs) {
        const o = el("option", m.title);
        o.value = m.id;
        mocs.append(o);
      }
      let selectedAction = "archive",
        previewStamp = 0;
      const confirm = control("确认执行此预览", async () => {
        if (!intent?.ready) return;
        const submitted = intent,
          result = await mutate(() =>
            api.action(submitted.operation_id, "apply", captured),
          );
        if (epoch !== generation) return;
        notify(
          result.action === "associate_only"
            ? "MOC 已关联，正文仍在当前位置。"
            : "归档已完成，索引等待更新。",
        );
      });
      const invalidate = () => {
        previewStamp++;
        intent = null;
        preview.replaceChildren();
        confirm.disabled = true;
        confirm.dataset.policyDisabled = "true";
      };
      invalidate();
      async function prepare(action) {
        const note = current.notes.find((n) => n.note_identity === notes.value),
          moc = current.mocs.find((m) => m.id === mocs.value);
        if (!note) throw new Error("没有可整理的笔记。");
        if (!["archive", "expert_archive"].includes(action) && !moc)
          throw new Error("请选择明确关联的 MOC。");
        const ticket = ++previewStamp;
        selectedAction = action;
        const packet = await api.preview({
          ...captured,
          note_identity: note.note_identity,
          expected_sha256: note.sha256,
          action,
          ...(!["archive", "expert_archive"].includes(action)
            ? { moc_id: moc.id, moc_revision: moc.revision }
            : {}),
          ...(destination.value ? { destination: destination.value } : {}),
        });
        if (epoch !== generation || stamp !== load || ticket !== previewStamp)
          return;
        intent = packet;
        preview.replaceChildren(
          el("p", phrase("当前位置：{p0}", { p0: packet.source_path })),
          el(
            "p",
            packet.target_path
              ? phrase("归档位置：{p0}", { p0: packet.target_path })
              : action === "associate_only"
                ? uiText("仅建立人工 MOC 关联，正文与图片位置保持。")
                : uiText("多个 MOC 的归档目录冲突，请选择后重新预览。"),
          ),
        );
        if (packet.expert_package)
          preview.append(
            el(
              "p",
              phrase(
                "完整专家 Draft：{p0} 个文件，整包移至完整专家 Draft 目录，包内字节和结构保持；知识仍未核实。",
                { p0: packet.package_files },
              ),
            ),
          );
        destination.replaceChildren();
        const choice = el("option", uiText("选择归档目录"));
        choice.value = "";
        destination.append(choice);
        for (const path of packet.destination_choices) {
          const o = el("option", path);
          o.value = path;
          destination.append(o);
        }
        destination.hidden = packet.destination_choices.length < 2;
        preview.append(
          el(
            "p",
            phrase("需改写的笔记或 MOC 链接：{p0}", {
              p0: packet.rewritten_notes.join("、") || "无",
            }),
          ),
        );
        for (const issue of packet.unresolved_links)
          preview.append(
            el(
              "p",
              phrase("待修复：{p0} → {p1}", {
                p0: issue.note_path,
                p1: issue.target,
              }),
              "error",
            ),
          );
        showJSON(preview, packet.file_mapping, "文件位置映射");
        confirm.dataset.policyDisabled = String(!packet.ready);
        confirm.disabled = !packet.ready;
      }
      notes.addEventListener("change", () => {
        destination.value = "";
        invalidate();
      });
      mocs.addEventListener("change", () => {
        destination.value = "";
        invalidate();
      });
      destination.addEventListener("change", invalidate);
      actions.append(
        control("仅关联 MOC", () => prepare("associate_only")),
        control("预览归档", () => prepare("archive")),
        control("关联并预览归档", () => prepare("link_archive")),
        control("按所选目录重新预览", () => prepare(selectedAction)),
      );
      const expertActions = el("div", null, "actions");
      expertActions.append(
        control("预览专家 Draft 整包归档", () => prepare("expert_archive")),
        control("关联并预览专家 Draft 整包归档", () =>
          prepare("expert_link_archive"),
        ),
      );
      const updateKind = () => {
        const expert =
          current.notes.find((n) => n.note_identity === notes.value)?.kind ===
          "expert";
        expertActions.hidden = !expert;
        for (const button of actions.querySelectorAll("button"))
          if (["预览归档", "关联并预览归档"].includes(button.textContent))
            button.hidden = expert;
      };
      notes.addEventListener("change", updateKind);
      updateKind();
      content.append(
        el("h4", uiText("当前笔记")),
        notes,
        mocs,
        actions,
        expertActions,
        destination,
        preview,
        confirm,
      );
      if (!current.notes.length)
        content.append(
          el(
            "p",
            uiText("暂无采用新布局的已发布笔记。旧目录包可在下方单独转换。"),
            "muted",
          ),
        );
      const historical = el("details"),
        legacy = el("select"),
        legacyPreview = el("div"),
        legacyActions = el("div", null, "actions");
      setText(legacy, uiText("逐项选择旧发布包"), "aria-label");
      historical.append(
        el("summary", uiText("可选：历史布局转换")),
        el(
          "p",
          uiText(
            "每次只转换一个明确选中的已发布包。执行前生成元数据备份，旧包与图片原件全部保留；本应用索引只展示新入口。",
          ),
        ),
        legacy,
        legacyActions,
        legacyPreview,
      );
      content.append(historical);
      let rows = [],
        migrationIntent = null,
        legacyEpoch = 0;
      const applyLegacy = control("确认备份并转换所选旧包", async () => {
        if (!migrationIntent?.ready) return;
        await mutate(() =>
          api.migrationAction(migrationIntent.operation_id, "apply", captured),
        );
        if (epoch === generation) {
          notify("所选旧包已转换；原包与附件保留，索引需要更新。");
        }
      });
      applyLegacy.disabled = true;
      applyLegacy.dataset.policyDisabled = "true";
      legacy.addEventListener("change", () => {
        legacyEpoch++;
        migrationIntent = null;
        legacyPreview.replaceChildren();
        applyLegacy.disabled = true;
        applyLegacy.dataset.policyDisabled = "true";
      });
      legacyActions.append(
        control("读取可转换的旧包", async () => {
          const ticket = ++legacyEpoch,
            values = await api.legacy(captured.client_id, captured.vault_id);
          if (epoch !== generation || stamp !== load || ticket !== legacyEpoch)
            return;
          rows = values;
          legacy.replaceChildren();
          rows.forEach((row, index) => {
            const option = el("option", row.source_path);
            option.value = String(index);
            legacy.append(option);
          });
          if (!rows.length)
            legacyPreview.replaceChildren(
              el("p", uiText("没有尚未转换的已发布包。")),
            );
        }),
        control("预览所选旧包转换", async () => {
          const row = rows[Number(legacy.value)];
          if (!row) throw new Error("请先读取并选择一个旧包。");
          const ticket = ++legacyEpoch,
            packet = await api.migrationPreview({
              ...captured,
              kind: row.kind,
              publication_id: row.publication_id,
            });
          if (epoch !== generation || stamp !== load || ticket !== legacyEpoch)
            return;
          migrationIntent = packet;
          legacyPreview.replaceChildren(
            el("p", `${packet.source_path} → ${packet.target_path}`),
            el(
              "p",
              phrase("保留的人工修改：{p0}", {
                p0: packet.modified_notes.join("、") || "无",
              }),
            ),
          );
          for (const issue of packet.unresolved_links)
            legacyPreview.append(
              el(
                "p",
                phrase("待修复：{p0} → {p1}", {
                  p0: issue.note_path,
                  p1: issue.target,
                }),
                "error",
              ),
            );
          showJSON(legacyPreview, packet.file_mapping, "转换文件位置");
          applyLegacy.dataset.policyDisabled = String(!packet.ready);
          applyLegacy.disabled = !packet.ready;
        }),
        applyLegacy,
      );
    }
    client.addEventListener("change", () => {
      externalSection();
      refresh().catch((error) => notify(error.message, true));
    });
    host.append(control("刷新整理状态", refresh));
    await refresh();
  }
  return {
    open,
    clear,
    get vaultId() {
      return selectedVault;
    },
  };
}
