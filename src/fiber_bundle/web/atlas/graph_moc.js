// Two-step MOC write-back (frozen preview → explicit confirm), reconciliation and
// contribution reversal. Mismatched digest or revision is rejected by the backend.
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  codeBlock,
  disclosure,
  empty,
  field,
  input,
  listRow,
  notice,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { sheet } from "./dialog.js";
import { captureDraftSave, rememberDraft } from "../unsaved_changes.js";

const t = strings({
  zh: {
    title: "确认 MOC 写回",
    undoTitle: "撤销本建议贡献",
    intro:
      "当前摘要或修订号不一致会拒绝写入。共享关联、人工归属和人工正文将保留。",
    acceptFirst: "请先接受并核对这组 MOC 线索。",
    moc_id: "MOC 标识（新建时保留生成值）",
    revision: "当前 MOC 修订号（新建为 0）",
    relative: "知识库内的 Markdown 路径",
    expected_sha256: "当前文件摘要（新文件填 absent）",
    preview: "生成写回预览",
    undoPreview: "预览撤销贡献",
    apply: "确认写入 MOC",
    cancel: "取消此预览",
    frozen: "预览已冻结；确认后才写入知识库。",
    handled: "该建议已处理。请查看写回记录。",
    written: "MOC 已写入，人工正文与人工归属保留。",
    writtenArchive:
      "MOC 已写入，归档预览已准备。请到知识库的“整理与迁移”核对后确认。",
    records: "MOC 写回记录",
    none: "暂无写回记录。",
    state: "写回状态：{state}",
    undo: "准备撤销贡献",
    reconcile: "对账已有写回",
    cancelPending: "取消待确认写回",
    binding: "写回绑定与版本",
    leave: "预览尚未确认，关闭会取消此预览。",
  },
  en: {
    title: "Confirm MOC write-back",
    undoTitle: "Reverse this suggestion's contribution",
    intro:
      "A mismatched digest or revision rejects the write. Shared links, human attribution and human text are kept.",
    acceptFirst: "Accept and check this MOC suggestion first.",
    moc_id: "MOC ID (keep the generated value for a new one)",
    revision: "Current MOC revision (0 for new)",
    relative: "Markdown path inside the vault",
    expected_sha256: "Current file digest (absent for a new file)",
    preview: "Generate write-back preview",
    undoPreview: "Preview reversal",
    apply: "Write MOC",
    cancel: "Cancel this preview",
    frozen: "Preview frozen; nothing is written until you confirm.",
    handled: "This suggestion was already handled. See the write-back records.",
    written: "MOC written; human text and attribution kept.",
    writtenArchive:
      "MOC written and archive previews prepared. Review them under Vaults → Organize & migrate.",
    records: "MOC write-back records",
    none: "No write-back records yet.",
    state: "Write-back: {state}",
    undo: "Prepare reversal",
    reconcile: "Reconcile write-back",
    cancelPending: "Cancel pending write-back",
    binding: "Write-back binding and versions",
    leave: "The preview isn't confirmed; closing cancels it.",
  },
});

export function createMocWriteback(graph, scope, epoch) {
  const list = h("div.a-stack");
  let pending = null;
  async function open(item, undo = null) {
    if (!undo && item.review.state !== "accepted")
      throw new Error(t("acceptFirst"));
    const captured = scope(),
      revision = epoch(),
      data = undo?.request;
    let defaultPath = "graph-moc.md";
    if (!data && graph.saveSettings) {
      const policy = await graph.saveSettings(captured.vault_id);
      if (revision !== epoch()) return;
      if (policy.publication_ready && policy.settings.policy_version === 2)
        defaultPath = policy.settings.moc_dir + "/graph-moc.md";
    }
    const fields = {
      moc_id: input({ value: data?.moc_id || crypto.randomUUID() }),
      revision: input({ value: String(data?.after_record.revision ?? 0) }),
      relative: input({ value: data?.path || defaultPath }),
      expected_sha256: input({ value: data?.preview_sha256 || "absent" }),
    };
    const previewHost = h("div.a-stack");
    const form = h(
      "div.a-form",
      { dataset: { draft: "tracked" } },
      h(
        "div.a-form-grid",
        Object.entries(fields).map(([key, node]) => field(t(key), node)),
      ),
    );
    const apply = button(t("apply"), { variant: "primary", iconName: "check" });
    apply.dataset.locked = "true";
    apply.disabled = true;
    let intent = null;
    const view = sheet({
      title: undo ? t("undoTitle") : t("title"),
      subtitle: captured.vault_id,
      wide: true,
      onClose: () => {
        if (intent?.state === "prepared") {
          toast(t("leave"), { tone: "info" });
          void graph
            .intentAction(intent.id, "cancel", captured)
            .finally(() => refresh(captured, revision));
        }
        pending = null;
      },
    });
    const make = button(undo ? t("undoPreview") : t("preview"), {
      iconName: "eye",
    });
    make.addEventListener("click", () =>
      run(make, async () => {
        const ack = captureDraftSave(form);
        const body = Object.fromEntries(
          Object.entries(fields).map(([key, node]) => [key, node.value]),
        );
        body.revision = Number(body.revision);
        const prepared = await graph.preview({
          ...captured,
          ...body,
          object_id: item.id,
          undo: undo?.id || null,
        });
        if (revision !== epoch()) return;
        intent = prepared;
        pending = prepared;
        ack();
        for (const node of Object.values(fields)) node.readOnly = true;
        clear(
          previewHost,
          notice(prepared.markdown ? t("frozen") : t("handled"), "info"),
          prepared.markdown ? codeBlock(prepared.markdown) : null,
        );
        apply.dataset.locked = String(prepared.state !== "prepared");
        apply.disabled = prepared.state !== "prepared";
      }),
    );
    apply.addEventListener("click", () =>
      run(apply, async () => {
        if (!intent) return;
        const result = await graph.intentAction(intent.id, "apply", {
          ...captured,
          confirm: true,
        });
        if (revision !== epoch()) return;
        toast(
          result.result?.archive_previews?.length
            ? t("writtenArchive")
            : t("written"),
          { tone: "success" },
        );
        intent = null;
        view.close();
        await refresh(captured, revision);
      }),
    );
    const cancel = button(t("cancel"), { variant: "ghost" });
    cancel.addEventListener("click", () =>
      run(cancel, async () => {
        if (intent?.state === "prepared")
          await graph.intentAction(intent.id, "cancel", captured);
        intent = null;
        view.close();
        await refresh(captured, revision);
      }),
    );
    view.body.append(
      notice(t("intro")),
      form,
      h("div.a-form-actions", make, apply, cancel),
      previewHost,
    );
    rememberDraft(form);
  }
  async function refresh(captured, revision) {
    const values = await graph.intents(captured.client_id, captured.vault_id);
    if (revision !== epoch()) return;
    clear(
      list,
      values.length
        ? h(
            "div.a-rows",
            values.map((row) => {
              const actions = [];
              const act = (label, work) => {
                const control = button(label, { variant: "small" });
                control.addEventListener("click", () => run(control, work));
                actions.push(control);
              };
              if (row.state === "completed" && row.request.action === "forward")
                act(t("undo"), () => open({ id: row.object_id }, row));
              if (["unknown", "applying"].includes(row.state))
                act(t("reconcile"), async () => {
                  await graph.intentAction(row.id, "reconcile", captured);
                  if (revision === epoch()) await refresh(captured, revision);
                });
              if (row.state === "prepared")
                act(t("cancelPending"), async () => {
                  await graph.intentAction(row.id, "cancel", captured);
                  if (revision === epoch()) await refresh(captured, revision);
                });
              return h(
                "div",
                listRow({
                  iconName: "graph",
                  title: row.request.path,
                  detail: t("state", { state: row.state }),
                  status: [
                    row.state,
                    row.state === "completed"
                      ? "success"
                      : ["unknown", "applying"].includes(row.state)
                        ? "error"
                        : "waiting",
                  ],
                  actions,
                }),
                h(
                  "div.a-pad",
                  disclosure(
                    t("binding"),
                    codeBlock(JSON.stringify(row, null, 2)),
                  ),
                ),
              );
            }),
          )
        : empty(t("none"), { iconName: "graph" }),
    );
  }
  return {
    list,
    open,
    refresh,
    clear: () => {
      list.replaceChildren();
      pending = null;
    },
    hasPending: () => pending?.state === "prepared",
    title: () => t("records"),
  };
}
