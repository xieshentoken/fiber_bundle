import { joinText, setText, uiText } from "./i18n/index.js";
// Saved source selection is scoped by owner/Vault; admission and execution are separate.
import { $, el, captureMessage } from "./ui.js";
import { control } from "./record_ui.js";
import { hasUnsavedChanges } from "./unsaved_changes.js";
import { confirmAction } from "./confirm_dialog.js";
export function createSourceView(api, refreshTasks) {
  let epoch = 0,
    policy = null,
    sources = [],
    requestId = null,
    initialized = false;
  const root = () => $("source-selection");
  function resetSources() {
    epoch++;
    sources = [];
    requestId = null;
    $("source-assets").replaceChildren();
    setText($("source-state"), uiText("请读取当前范围的已保存来源。"));
  }
  async function load() {
    resetSources();
    const current = epoch;
    const client = $("source-client").value,
      vault = $("source-vault").value;
    setText($("source-state"), uiText("正在读取已验证来源…"));
    try {
      const rows = await api.sources(client, vault);
      if (current !== epoch) return;
      sources = rows;
      requestId = window.crypto.randomUUID();
      for (const [i, item] of rows.entries()) {
        const option = el(
          "option",
          joinText([
            "",
            item.name,
            " · ",
            item.kind === "article"
              ? uiText("文章链接")
              : item.kind === "document"
                ? uiText("文档/文字候选")
                : uiText("当前类型待处理"),
            "",
          ]),
        );
        option.value = String(i);
        $("source-assets").append(option);
      }
      setText(
        $("source-state"),
        rows.length
          ? uiText("准备任务仅冻结来源；执行和发布分别确认。")
          : uiText("当前范围没有可处理的本人来源。"),
      );
      captureMessage("documents", "source-selection")("");
    } catch (error) {
      if (current !== epoch) return;
      const text =
        error.status === 404
          ? "所选身份没有当前来源的新增/原件权限。"
          : error.message;
      setText($("source-state"), uiText(text));
      captureMessage("documents", "source-selection")(text, true);
    }
  }
  async function refresh() {
    const current = ++epoch;
    const [clients, vaults, next] = await Promise.all([
      api.clients(),
      api.vaults(),
      api.sourcePolicy(),
    ]);
    if (current !== epoch) return;
    if (!initialized || !hasUnsavedChanges($("source-policy-form")))
      policy = next;
    if (!initialized) {
      root().replaceChildren(el("h3", uiText("处理已保存来源")));
      for (const [id, title] of [
        ["source-client", "来源身份"],
        ["source-vault", "知识库"],
        ["source-assets", "已保存来源"],
      ]) {
        const label = el("label", uiText(title)),
          select = el("select");
        select.id = id;
        label.append(select);
        root().append(label);
        if (id !== "source-assets")
          select.addEventListener("change", resetSources);
        else
          select.addEventListener(
            "change",
            () => (requestId = window.crypto.randomUUID()),
          );
      }
      const state = el("p");
      state.id = "source-state";
      root().append(
        state,
        control("读取已保存来源", load),
        control("准备来源处理任务", async (notify) => {
          if (hasUnsavedChanges($("source-policy-form")))
            throw new Error("请先保存或撤回提取配置的修改。");
          if (!policy?.configured)
            throw new Error(
              "请先保存提取配置及明确的云端执行模型配置。自动执行仍关闭。",
            );
          const item = sources[Number($("source-assets").value)];
          if (!item || !["document", "article"].includes(item.kind))
            throw new Error(
              "请选择当前支持的明确来源；图片 OCR 尚需单独配置。",
            );
          const fixed = epoch;
          const result = await api.submitSource({
            client_id: $("source-client").value,
            vault_id: $("source-vault").value,
            receipt_id: item.receipt_id,
            asset_id: item.asset_id,
            source_sha256: item.sha256,
            kind: item.kind,
            submission_id: requestId,
            configuration_sha256: policy.configuration_sha256,
            model_configuration_sha256: policy.model_configuration_sha256,
          });
          if (fixed !== epoch) return;
          notify("任务已准备，尚未调用模型或发布。");
          await refreshTasks();
          const start = control("确认执行这项来源任务", async (feedback) => {
            if (
              !(await confirmAction({
                title: "执行此固定来源任务？",
                body: "会使用已配置的模型费用额度，完成后仍需人工审核。",
                confirmLabel: "执行来源任务",
              }))
            )
              return;
            await api.startSource(result.run_id);
            feedback("已提交有限执行；刷新查看候选，未自动发布。");
            await refreshTasks();
          });
          root().append(start);
        }),
      );
      initialized = true;
    }
    for (const [id, items] of [
      ["source-client", clients.filter((c) => c.active)],
      ["source-vault", vaults.filter((v) => v.status === "active")],
    ]) {
      const select = $(id),
        previous = select.value;
      select.replaceChildren();
      for (const item of items) {
        const option = el("option", item.name);
        option.value = item.id;
        select.append(option);
      }
      if ([...select.options].some((o) => o.value === previous))
        select.value = previous;
      else resetSources();
    }
  }
  return {
    refresh,
    clear() {
      epoch++;
      initialized = false;
      policy = null;
      sources = [];
      root().replaceChildren();
    },
  };
}
