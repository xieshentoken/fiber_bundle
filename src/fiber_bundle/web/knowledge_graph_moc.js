import { phrase, setText, translate, uiText } from "./i18n/index.js";
// Two-step MOC preview/confirmation, plus explicit reconciliation and contribution reversal.
import { $, el, captureMessage } from "./ui.js";
import { control, showJSON } from "./record_ui.js";
import {
  rememberDraft,
  captureDraftSave,
  hasUnsavedChanges,
} from "./unsaved_changes.js";
export function createGraphMocView(graph, scope, epoch) {
  const root = $("graph-moc-form"),
    list = $("graph-writebacks");
  let intent = null;
  let opening = 0;
  function clear() {
    opening++;
    root.replaceChildren();
    list.replaceChildren();
    intent = null;
  }
  async function open(item, undo = null) {
    if (!undo && item.review.state !== "accepted") {
      captureMessage("graph")("请先接受并核对这组 MOC 线索。", true);
      return;
    }
    if (
      (hasUnsavedChanges(root) || intent?.state === "prepared") &&
      !window.confirm(
        translate("MOC 表单有未保存的内容，切换会清除这些更改。确定继续？"),
      )
    )
      return;
    root.replaceChildren();
    intent = null;
    const captured = scope(),
      revision = epoch(),
      form = el("div"),
      fields = {};
    form.dataset.draft = "tracked";
    const data = undo?.request;
    const token = ++opening;
    let defaultPath = "graph-moc.md";
    if (!data && graph.saveSettings) {
      try {
        const policy = await graph.saveSettings(captured.vault_id);
        if (token !== opening || revision !== epoch()) return;
        if (policy.publication_ready && policy.settings.policy_version === 2)
          defaultPath = policy.settings.moc_dir + "/graph-moc.md";
      } catch (error) {
        if (token === opening && revision === epoch())
          captureMessage("graph")(error.message, true);
        return;
      }
    }
    for (const [key, label, value] of [
      [
        "moc_id",
        "MOC 标识（新建时保留生成值）",
        data?.moc_id || window.crypto.randomUUID(),
      ],
      [
        "revision",
        "当前 MOC 修订号（新建为 0）",
        data?.after_record.revision ?? 0,
      ],
      ["relative", "Vault 内的 Markdown 路径", data?.path || defaultPath],
      [
        "expected_sha256",
        "当前文件摘要（新文件填 absent）",
        data?.preview_sha256 || "absent",
      ],
    ]) {
      const node = el("input");
      node.value = String(value);
      setText(node, uiText(label), "aria-label");
      const wrapper = el("label", uiText(label));
      wrapper.append(node);
      form.append(wrapper);
      fields[key] = node;
    }
    const preview = el("pre"),
      message = () => captureMessage("graph");
    const confirm = control("确认写入 MOC", async () => {
      if (!intent) return;
      const current = intent;
      let result;
      try {
        result = await graph.intentAction(current.id, "apply", {
          ...captured,
          confirm: true,
        });
      } catch (error) {
        if (revision === epoch()) {
          setText(
            preview,
            phrase(
              "MOC 写入未完成：{p0}。请核对下方写回记录后恢复，不要重复创建。",
              { p0: uiText(error.message) },
            ),
          );
          await refresh(captured, revision);
        }
        throw error;
      }
      if (revision !== epoch()) return;
      message()(
        result.result?.archive_previews?.length
          ? "MOC 已写入，归档预览已准备。请到知识库的“整理与布局迁移”核对后确认。"
          : "MOC 已写入，人工正文与人工归属保留。",
      );
      root.replaceChildren();
      intent = null;
      await refresh(captured, revision);
    });
    confirm.disabled = true;
    const actions = el("div", null, "panel-actions");
    actions.append(
      control(undo ? "预览撤销贡献" : "生成写回预览", async () => {
        const ack = captureDraftSave(form),
          body = Object.fromEntries(
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
        ack();
        setText(
          preview,
          uiText(prepared.markdown || "该建议已处理。请查看写回记录。"),
        );
        for (const node of Object.values(fields)) node.readOnly = true;
        confirm.disabled = prepared.state !== "prepared";
        message()("预览已冻结；确认后才写入 Vault。");
      }),
      confirm,
      control("取消此预览", async () => {
        if (intent?.state === "prepared")
          await graph.intentAction(intent.id, "cancel", captured);
        if (revision !== epoch()) return;
        root.replaceChildren();
        intent = null;
        await refresh(captured, revision);
      }),
    );
    form.append(actions);
    root.append(
      el("h3", undo ? uiText("撤销本建议贡献") : uiText("确认 MOC 写回")),
      el(
        "p",
        uiText(
          "当前摘要或修订号不一致会拒绝写入。共享关联、人工归属和人工正文将保留。",
        ),
      ),
      form,
      preview,
    );
    rememberDraft(form);
  }
  async function refresh(captured, revision) {
    const values = await graph.intents(captured.client_id, captured.vault_id);
    if (revision !== epoch()) return;
    list.replaceChildren();
    for (const row of values) {
      const card = el("article", null, "row record");
      card.append(
        el("h3", row.request.path),
        el("p", phrase("写回状态：{p0}", { p0: uiText(row.state) })),
      );
      if (row.state === "completed" && row.request.action === "forward")
        card.append(
          control("准备撤销本建议贡献", () => open({ id: row.object_id }, row)),
        );
      if (["unknown", "applying"].includes(row.state))
        card.append(
          control("对账已有写回", async () => {
            await graph.intentAction(row.id, "reconcile", captured);
            if (revision === epoch()) await refresh(captured, revision);
          }),
        );
      if (row.state === "prepared")
        card.append(
          control("取消待确认写回", async () => {
            await graph.intentAction(row.id, "cancel", captured);
            if (revision === epoch()) await refresh(captured, revision);
          }),
        );
      showJSON(card, row, "写回绑定与版本");
      list.append(card);
    }
  }
  return {
    open,
    refresh,
    clear,
    hasPending: () => intent?.state === "prepared",
  };
}
