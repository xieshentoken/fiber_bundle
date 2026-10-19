import { phrase, setText, translate, uiText } from "./i18n/index.js";
import { confirmPublication } from "./publication_preview.js";
// Literal article preview and exact review binding; no HTML execution or auto publication.
import { $, el, checkbox, action, captureMessage, setBusy } from "./ui.js";
import { control } from "./record_ui.js";
import {
  rememberDraft,
  captureDraftSave,
  hasUnsavedChanges,
} from "./unsaved_changes.js";
import { confirmAction } from "./confirm_dialog.js";
export function createArticlesView(api) {
  let epoch = 0;
  const root = () => $("article-list");
  async function open(task) {
    if (
      hasUnsavedChanges(root()) &&
      !window.confirm(translate("文章审核还有未保存内容，确定切换？"))
    )
      return;
    const current = ++epoch,
      packet = await api.articlePacket(task.run_id);
    if (current !== epoch) return;
    const card = el("article", null, "document-review");
    card.append(
      el("h3", packet.title),
      el(
        "p",
        phrase("{p0} · 发布日期：{p1}", {
          p0: packet.author || "作者未知",
          p1: packet.published || "未知",
        }),
      ),
      el("pre", packet.markdown),
    );
    const original = el("a", uiText("取回保全 HTML 核对原文"));
    original.href =
      "/admin/articles/" + encodeURIComponent(task.run_id) + "/source";
    original.download = "";
    card.append(original);
    for (const image of packet.images) {
      const node = el("img");
      node.src =
        "/admin/articles/" +
        encodeURIComponent(task.run_id) +
        "/images/" +
        encodeURIComponent(image.sha256);
      node.alt = "原文图片 " + image.index;
      node.loading = "lazy";
      card.append(node);
    }
    if (packet.publication) {
      card.append(
        el(
          "p",
          phrase("发布状态：{p0} · {p1}", {
            p0: uiText(packet.publication.state),
            p1: packet.publication.relative_path,
          }),
        ),
      );
      root().replaceChildren(card);
      return;
    }
    const form = el("form"),
      reason = el("textarea"),
      checks = [
        checkbox("已核对原件及完整性", false),
        checkbox("已逐段核对正文", false),
        checkbox("已核对全部图片及顺序", false),
      ];
    form.dataset.draft = "tracked";
    reason.required = true;
    reason.maxLength = 2000;
    setText(reason, uiText("文章审核理由"), "aria-label");
    const save = el("button", uiText("记录文章人工审核")),
      publish = el("button", uiText("确认发布已审核文章")),
      gate = el("p");
    save.type = "submit";
    publish.type = "button";
    let reviewId = packet.review_id;
    publish.disabled = !packet.review_ready;
    for (const check of checks) form.append(check.label);
    form.append(reason, save, publish, gate);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const notify = captureMessage("documents"),
        acknowledge = captureDraftSave(form);
      save.disabled = true;
      setBusy(save, true);
      try {
        if (!checks.every((c) => c.input.checked) || !reason.value.trim())
          throw new Error("请核对原件、正文、全部图片并填写实际审核理由。");
        const result = await api.reviewArticle(task.run_id, {
          manifest_sha256: packet.manifest_sha256,
          source_reviewed: true,
          text_reviewed: true,
          images_reviewed: true,
          reason: reason.value,
        });
        if (current !== epoch) return;
        acknowledge();
        reviewId = result.review_id;
        publish.disabled = !result.review_ready || hasUnsavedChanges(form);
        setText(gate, uiText("人工审核已记录；尚未独立核验或发布。"));
        notify("文章人工审核已记录。");
      } catch (error) {
        if (current === epoch) notify(error.message, true);
      } finally {
        save.disabled = false;
        setBusy(save, false);
      }
    });
    action(publish, async (notify) => {
      if (hasUnsavedChanges(form))
        throw new Error("请先保存当前文章审核判断。");
      if (
        !(await confirmPublication(api, "article", task.run_id, {
          review_id: reviewId,
          manifest_sha256: packet.manifest_sha256,
        }))
      )
        return;
      if (current !== epoch) return;
      const result = await api.publishArticle(task.run_id, {
        review_id: reviewId,
        manifest_sha256: packet.manifest_sha256,
        confirm: true,
      });
      if (current !== epoch) return;
      publish.dataset.policyDisabled = "true";
      save.disabled = true;
      setText(gate, phrase("已发布：{p0}", { p0: result.note_path }));
      notify("文章已发布，独立核验状态保持未进行。");
    });
    card.append(form);
    root().replaceChildren(card);
    rememberDraft(form);
  }
  async function refresh() {
    if (
      hasUnsavedChanges(root()) &&
      !window.confirm(translate("文章审核还有未保存内容，确定重新读取？"))
    )
      return;
    const current = ++epoch,
      data = await api.articles();
    if (current !== epoch) return;
    root().replaceChildren(el("h3", uiText("文章任务")));
    if (!data.tasks.length)
      root().append(
        el("p", uiText("暂无文章任务。从已保存的文章链接准备任务。")),
      );
    for (const task of data.tasks) {
      const card = el("article", null, "row");
      card.dataset.taskKind = "articles";
      card.dataset.taskId = task.run_id;
      card.append(el("p", task.run_state + " · " + task.state));
      if (task.run_state === "succeeded" && task.state === "captured")
        card.append(control("预览与核对文章图文", () => open(task)));
      if (task.run_state === "queued")
        card.append(
          control("确认执行文章抓取", async () => {
            if (
              await confirmAction({
                title: "执行固定文章抓取任务？",
                body: "会使用已配置的模型额度。",
                confirmLabel: "执行抓取",
              })
            ) {
              await api.startSource(task.run_id);
              await refresh();
            }
          }),
        );
      card.append(
        control("取消文章任务", async () => {
          if (
            await confirmAction({
              title: "取消此文章任务？",
              body: "原件和费用保留。",
              confirmLabel: "取消任务",
              cancelLabel: "保留任务",
              tone: "danger",
            })
          ) {
            await api.cancelExecution(task.run_id);
            await refresh();
          }
        }),
      );
      root().append(card);
    }
  }
  return {
    refresh,
    clear() {
      epoch++;
      root().replaceChildren();
    },
  };
}
