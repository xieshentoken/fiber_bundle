import { phrase, setText, textNode, translate, uiText } from "./i18n/index.js";
import { confirmPublication } from "./publication_preview.js";
// Human source review only. Content stays literal; no model calls or automatic approval.
import {
  captureDraftSave,
  rememberDraft,
  hasUnsavedChanges,
} from "./unsaved_changes.js";
import { $, action, checkbox, el, message, setBusy } from "./ui.js";
import { createSourceView } from "./source_selection.js";
import { createArticlesView } from "./articles.js";
import { confirmAction } from "./confirm_dialog.js";
import {
  documentState,
  publicationState,
  runState,
  shortId,
} from "./state_labels.js";

export function assessment(packet, sourceChecked, visualChecked, decisions) {
  if (!sourceChecked || !visualChecked)
    throw new Error("请先核对原件与视觉内容。");
  if (
    decisions.length !== packet.units.length ||
    decisions.some((d) => !d.reason.trim())
  )
    throw new Error("请为每个来源单元填写判断与理由。");
  return {
    source_sha256: packet.source_sha256,
    result_sha256: packet.result_sha256,
    source_reviewed: true,
    visual_reviewed: true,
    judgments: decisions,
  };
}

export function createDocumentsView(api) {
  let revision = 0;
  const articles = createArticlesView(api);
  const sources = createSourceView(api, refresh);
  const container = () => $("document-list");
  function clear() {
    revision++;
    container().replaceChildren();
    sources.clear();
    articles.clear();
  }
  function renderPacket(task, packet, current) {
    if (current !== revision) return;
    const root = el("article", null, "document-review"),
      heading = el("h3", uiText("来源核对")),
      gate = el("p"),
      original = el("a", uiText("下载原件并核对"));
    original.href =
      "/admin/documents/" + encodeURIComponent(task.run_id) + "/source";
    original.download = "";
    setText(
      gate,
      packet.review_gate.publication_ready
        ? uiText("人工审核已记录；仍未独立核验。可显式发布。")
        : uiText("提取候选尚未通过发布审核。"),
    );
    root.append(heading, gate, original);
    if (packet.candidate.complete === false)
      root.append(
        el(
          "p",
          uiText("来源覆盖不完整，补交完整来源后新建任务。当前候选不能发布。"),
          "error",
        ),
      );
    if (packet.missing_references?.length)
      root.append(
        el(
          "p",
          phrase("待补充附件或链接映射：{p0}", {
            p0: packet.missing_references.join("、"),
          }),
          "error",
        ),
      );
    const form = el("form"),
      source = checkbox("我已核对原件、来源顺序及完整性", false),
      visual = checkbox("我已核对图像、数字、单位、表格与公式", false),
      decisions = [];
    const texts = packet.candidate.pages || packet.candidate.segments;
    for (let i = 0; i < packet.units.length; i++) {
      const unit = packet.units[i],
        block = el("fieldset"),
        select = el("select"),
        reason = el("textarea"),
        locator = texts[i].locator || { physical_page: texts[i].physical_page };
      block.append(
        el("legend", phrase("来源单元 {p0}", { p0: i + 1 })),
        el("pre", JSON.stringify(locator, null, 2)),
        el("pre", texts[i].text),
      );
      for (const [value, title] of [
        ["uncertain", "待核对"],
        ["reject", "不接受"],
        ["accept", "已核对，接受"],
      ]) {
        const option = el("option", uiText(title));
        option.value = value;
        select.append(option);
      }
      setText(
        select,
        phrase("来源单元 {p0} 判断", { p0: i + 1 }),
        "aria-label",
      );
      setText(
        reason,
        phrase("来源单元 {p0} 理由", { p0: i + 1 }),
        "aria-label",
      );
      setText(reason, uiText("记录实际核对理由"), "placeholder");
      reason.required = true;
      reason.maxLength = 2000;
      block.append(el("p", unit.review_reasons.join(" · ")), select, reason);
      decisions.push({ unit, select, reason });
      form.append(block);
    }
    for (const item of packet.candidate.attachments || []) {
      const image = el("img");
      image.src =
        "/admin/documents/" +
        encodeURIComponent(task.run_id) +
        "/images/" +
        encodeURIComponent(item.sha256);
      image.alt =
        item.role === "page_render" ? "原页图像，需人工核对" : "源文档内嵌图像";
      image.loading = "lazy";
      form.append(image);
    }
    const save = el("button", uiText("记录人工审核")),
      publish = el("button", uiText("发布已审核文档"));
    save.type = "submit";
    publish.type = "button";
    publish.disabled = !packet.review_gate.publication_ready;
    const publicationStatus = el("p");
    publicationStatus.setAttribute("role", "status");
    publicationStatus.setAttribute("aria-live", "polite");
    const actions = el("div", null, "panel-actions");
    actions.append(save, publish);
    form.append(source.label, visual.label, actions, publicationStatus);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const acknowledge = captureDraftSave(form);
      save.disabled = true;
      setBusy(save, true);
      try {
        const body = assessment(
          packet,
          source.input.checked,
          visual.input.checked,
          decisions.map(({ unit, select, reason }) => ({
            unit_id: unit.unit_id,
            verdict: select.value,
            reason: reason.value,
          })),
        );
        const result = await api.reviewDocument(task.run_id, body);
        if (current !== revision) return;
        acknowledge();
        publish.disabled =
          !result.review_gate.publication_ready || hasUnsavedChanges(form);
        setText(
          gate,
          result.review_gate.publication_ready
            ? uiText("人工审核已记录；可显式发布。独立核验仍未进行。")
            : uiText("审核已记录，仍有未接受单元或不完整来源。"),
        );
        message("人工判断已记录。", false, "documents");
      } catch (error) {
        if (current === revision) message(error.message, true, "documents");
      } finally {
        setBusy(save, false);
        if (current === revision) save.disabled = false;
      }
    });
    publish.addEventListener("click", async () => {
      if (hasUnsavedChanges(form)) {
        message(
          "审核表单还有未保存判断，请先记录人工审核。",
          true,
          "documents",
        );
        return;
      }
      publish.disabled = true;
      setBusy(publish, true);
      try {
        if (!(await confirmPublication(api, "document", task.run_id))) return;
        if (current !== revision) return;
        const result = await api.publishDocument(task.run_id);
        if (current !== revision) return;
        setText(
          gate,
          phrase("已发布：{p0}。人工审核记录已冻结，独立核验仍未进行。", {
            p0: result.entry_note_path || result.relative_path,
          }),
        );
        setText(publicationStatus, uiText(gate.textContent));
        publicationStatus.className = "";
        publicationStatus.setAttribute("role", "status");
        save.disabled = true;
      } catch (error) {
        if (current === revision) {
          setText(publicationStatus, uiText(error.message));
          publicationStatus.className = "error";
          publicationStatus.setAttribute("role", "alert");
          message(error.message, true, "documents");
          publish.disabled = false;
        }
      } finally {
        setBusy(publish, false);
      }
    });
    form.dataset.draft = "tracked";
    rememberDraft(form);
    root.append(form);
    container().replaceChildren(root);
  }
  async function refresh() {
    if (
      hasUnsavedChanges(container()) &&
      !window.confirm(translate("人工审核还有未保存内容，确定重新读取？"))
    )
      return;
    const current = ++revision;
    const response = await api.documents();
    if (current !== revision) return;
    const rows = response.tasks.map((task) => {
      const row = el("article", null, "ui-task"),
        open = el("button", uiText("核对来源")),
        actions = el("div", null, "ui-task-actions");
      row.dataset.taskKind = "documents";
      row.dataset.taskId = task.run_id;
      const [run, runTone] = runState(task.run_state),
        [doc, docTone] = documentState(task.state),
        main = el("div", null, "ui-task-main"),
        id = el(
          "span",
          phrase("任务 {p0}", { p0: shortId(task.run_id) }),
          "technical",
        );
      id.title = task.run_id;
      const meta = el("small");
      meta.append(
        textNode(
          uiText(
            [
              task.binding_kind === "document" ? "文档制作" : "来源提取",
              Number.isInteger(task.page_count)
                ? task.page_count + " 页"
                : null,
            ]
              .filter(Boolean)
              .join(" · ") + " · ",
          ),
        ),
        id,
      );
      main.append(el("strong", uiText("文档任务")), meta);
      const states = el("div", null, "ui-task-states");
      states.append(el("span", uiText(run), "status-badge " + runTone));
      if (task.run_state === "succeeded")
        states.append(el("span", uiText(doc), "status-badge " + docTone));
      if (task.publication_state) {
        const [pub, pubTone] = publicationState(task.publication_state);
        states.append(el("span", uiText(pub), "status-badge " + pubTone));
      }
      row.append(main, states, actions);
      actions.append(open);
      if (task.run_state === "queued" && task.binding_kind !== "document") {
        const start = el("button", uiText("确认执行来源提取"));
        action(start, async () => {
          if (
            !(await confirmAction({
              title: "执行此固定来源提取任务？",
              body: "会使用已配置的模型额度。",
              confirmLabel: "执行提取",
            }))
          )
            return;
          await api.startSource(task.run_id);
          await refresh();
        });
        start.className = "primary";
        actions.append(start);
      }
      const cancel = el("button", uiText("取消文档任务"), "danger");
      action(cancel, async () => {
        if (
          !(await confirmAction({
            title: "取消此文档任务？",
            body: "原件和费用保留。",
            confirmLabel: "取消任务",
            cancelLabel: "保留任务",
            tone: "danger",
          }))
        )
          return;
        await api.cancelExecution(task.run_id);
        await refresh();
      });
      actions.append(cancel);
      open.disabled =
        task.run_state !== "succeeded" || task.state !== "extracted";
      action(open, async () => {
        if (
          hasUnsavedChanges(container()) &&
          !window.confirm(translate("人工审核还有未保存内容，确定切换？"))
        )
          return;
        const selected = ++revision;
        const packet = await api.documentPacket(task.run_id);
        renderPacket(task, packet, selected);
      });
      return row;
    });
    container().replaceChildren(
      ...(rows.length
        ? rows
        : [
            el(
              "p",
              uiText("暂无文档提取任务。请从本人已保存的来源准备任务。"),
              "empty",
            ),
          ]),
    );
    await Promise.all([sources.refresh(), articles.refresh()]);
  }
  return { clear, refresh };
}
