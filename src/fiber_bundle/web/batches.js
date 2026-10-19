import { joinText, phrase, uiText } from "./i18n/index.js";
import { $, el, captureMessage, setBusy } from "./ui.js";
import { reconcileChildren } from "./record_ui.js";
import { createBatchCountdown } from "./batch_countdown.js";
import { batchProgress, createBatchProgressRefresh } from "./batch_progress.js";
import {
  rememberDraft,
  captureDraftSave,
  hasUnsavedChanges,
} from "./unsaved_changes.js";
import { confirmAction } from "./confirm_dialog.js";

export function renderPendingBatches(
  batches,
  api,
  onUpdate,
  container = $("batch-list"),
) {
  container.replaceChildren();
  if (!batches.length) {
    container.append(el("p", uiText("暂无收件批次。"), "empty"));
    return;
  }
  for (const batch of batches) {
    const card = el("article", null, "batch-card");
    card.dataset.taskKind = "batch";
    card.dataset.taskId = batch.id;
    card.dataset.batchId = batch.id;

    if (batch.state === "buffering" && Number.isFinite(batch.freeze_at)) {
      const remaining = batch.freeze_at * 1000 - Date.now();
      if (remaining > 0) {
        const progress = el("div", null, "batch-timer");
        progress.setAttribute("aria-hidden", "true");
        const bar = el("div", null, "timer-bar");
        bar.style.animation = `timer-countdown ${remaining}ms linear forwards`;
        progress.append(bar);
        card.append(
          progress,
          el(
            "p",
            phrase("预计核对时间：{p0}；以后台冻结状态为准。", {
              p0: new Date(batch.freeze_at * 1000).toLocaleTimeString("zh-CN"),
            }),
            "muted",
          ),
        );
      } else {
        card.append(
          el("p", uiText("缓冲时间已到，等待后台确认冻结。"), "muted"),
        );
      }
    }
    const heading = el(
      "h3",
      phrase("{p0} 个接收事件", { p0: batch.event_count ?? batch.count }),
    );
    const progress = batchProgress(batch);
    const status =
      batch.store_state === "stored"
        ? progress
          ? "原件已归档"
          : "原件已保存；笔记需另行审核发布"
        : batch.state === "selected" && !batch.needs_question
          ? "操作已选择；等待处理"
          : batch.state === "buffering"
            ? "等待批次冻结"
            : batch.needs_question
              ? "待补充咨询问题"
              : batch.content_ready
                ? "待确认"
                : "内容待补齐";
    const stamp = new Date(batch.received_at * 1000);
    const detail = el(
      "p",
      phrase("{p0} · 收到时间：{p1}", {
        p0: uiText(status),
        p1: stamp.toLocaleString("zh-CN"),
      }),
      "muted",
    );
    const preview = el("p", batch.preview, "batch-preview");
    card.append(heading, detail, preview);
    if (progress) {
      const status = el("div", null, "batch-progress");
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      status.append(el("p", progress.stage));
      for (const reason of progress.reasons)
        status.append(el("p", uiText(reason), "error"));
      card.append(status);
    }
    if (batch.platform)
      card.append(
        el(
          "p",
          joinText([
            "",
            uiText(
              batch.platform === "feishu" ? uiText("飞书") : uiText("微信"),
            ),
            " · ",
            batch.client_name || batch.client_id,
            " · ",
            batch.vault_name || batch.vault_id,
            "",
          ]),
        ),
      );
    if (batch.received_file_count !== undefined)
      card.append(
        el(
          "p",
          phrase(
            "已取得文件 {p0} 个 · ZIP 成员 {p1} 个；原聊天消息数与完整性未知。",
            { p0: batch.received_file_count, p1: batch.archive_member_count },
          ),
        ),
      );
    if (batch.processing_blocker === "requires_explicit_store_quota")
      card.append(
        el(
          "p",
          uiText("等待明确的 Store 处理额度；选择操作不会自动授予额度。"),
          "muted",
        ),
      );
    if (batch.processing_blocker === "receiver_unavailable")
      card.append(
        el("p", uiText("接收器状态不可用，当前无法确认处理进度。"), "error"),
      );
    if (batch.processing_blocker === "store_quota_zero")
      card.append(
        el("p", uiText("Store 剩余额度为零，原件保存尚未执行。"), "muted"),
      );
    if (batch.processing_state && !progress)
      card.append(
        el(
          "p",
          phrase("来源任务：{p0} · 发布：{p1}", {
            p0: batch.processing_state,
            p1: batch.publication_state,
          }),
        ),
      );
    if (batch.attachments?.length) {
      const list = el("details");
      list.append(el("summary", uiText("附件结构")));
      for (const item of batch.attachments)
        list.append(
          el(
            "p",
            item.name
              ? joinText([
                  "",
                  item.name,
                  " · ",
                  item.media_type,
                  " · ",
                  item.bytes,
                  " bytes",
                ])
              : phrase("附件 {p0}，缺件 {p1}", {
                  p0: uiText(item.state),
                  p1: item.missing_files ?? "未知",
                }),
          ),
        );
      card.append(list);
    }
    if (batch.needs_question) {
      const form = el("form", null, "batch-question");
      form.dataset.draft = "tracked";
      const label = el("label", uiText("咨询问题"));
      const input = el("input");
      const button = el("button", uiText("补充问题"), "primary");
      const cancel = el("button", uiText("取消批次"), "quiet");
      input.required = true;
      input.maxLength = 4000;
      label.append(input);
      button.type = "submit";
      cancel.type = "button";
      form.append(label, button, cancel);
      rememberDraft(form);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const notify = captureMessage("batches");
        const acknowledge = captureDraftSave(form);
        button.disabled = true;
        setBusy(button, true);
        try {
          await api.chooseBatch(batch.id, "consult", input.value);
          acknowledge();
          await refreshAfterChoice(
            onUpdate,
            notify,
            "咨询问题已记录；尚未交给模型处理。",
          );
        } catch (error) {
          notify(error.message, true);
        } finally {
          setBusy(button, false);
          button.disabled = form.dataset.questionFixed === "true";
        }
      });
      cancel.addEventListener("click", async () => {
        const notify = captureMessage("batches");
        if (
          !(await confirmAction({
            title: "取消本批次？",
            body: "原件会保留。",
            confirmLabel: "取消本批次",
            cancelLabel: "保留批次",
            tone: "danger",
          }))
        )
          return;
        button.disabled = true;
        cancel.disabled = true;
        setBusy(cancel, true);
        try {
          await api.chooseBatch(batch.id, "cancel");
          await refreshAfterChoice(
            onUpdate,
            notify,
            "批次已取消；原件仍保留。",
          );
        } catch (error) {
          notify(error.message, true);
        } finally {
          button.disabled = false;
          setBusy(cancel, false);
          cancel.disabled = false;
        }
      });
      card.append(form);
    } else {
      const actions = el("div", null, "batch-actions");
      const choices = [
        ["保存为一份", "save_one"],
        ["分别保存", "save_separately"],
        ["仅用于咨询", "consult"],
        ["取消", "cancel"],
      ];
      const available = (choice) =>
        !(progress && choice === "cancel" && batch.store_state === "stored") &&
        (batch.state === "frozen" ||
          (choice === "cancel" && batch.state === "selected") ||
          (progress?.canContinue && choice === batch.action)) &&
        (choice === "cancel" || batch.content_ready);
      for (const [label, choice] of choices) {
        const continuing = progress?.canContinue && choice === batch.action;
        const button = el(
          "button",
          continuing ? uiText("继续此批次入库") : uiText(label),
          choice === "cancel" ? "quiet" : "",
        );
        button.type = "button";
        button.disabled = !available(choice);
        button.addEventListener("click", async () => {
          const notify = captureMessage("batches");
          if (
            choice === "cancel" &&
            !(await confirmAction({
              title: "取消本批次？",
              body: "原件会保留。",
              confirmLabel: "取消本批次",
              cancelLabel: "保留批次",
              tone: "danger",
            }))
          )
            return;
          for (const item of actions.querySelectorAll("button"))
            item.disabled = true;
          setBusy(button, true);
          try {
            const result = await api.chooseBatch(batch.id, choice);
            await refreshAfterChoice(
              onUpdate,
              notify,
              choice === "cancel"
                ? "批次已取消；原件仍保留。"
                : result.intake
                  ? "入库请求已记录；处理进度与失败原因显示在此批次中。"
                  : "选择已记录；资料尚未入库，咨询尚未执行。",
            );
          } catch (error) {
            notify(error.message, true);
            for (const item of actions.querySelectorAll("button")) {
              item.disabled = !available(item.dataset.choice);
            }
          } finally {
            setBusy(button, false);
          }
        });
        button.dataset.choice = choice;
        actions.append(button);
      }
      card.append(actions);
    }
    container.append(card);
  }
}

async function refreshAfterChoice(onUpdate, notify, successText) {
  try {
    await onUpdate();
    notify(successText);
  } catch {
    notify("选择已记录，但列表刷新失败，请重新刷新页面。", true);
  }
}

export function createPendingBatchesView(api, onUpdate) {
  const records = new Map();
  $("delivery-reconcile")?.addEventListener("click", async () => {
    const box = $("delivery-reconciliation");
    box.replaceChildren();
    const notify = captureMessage("batches", "delivery-reconciliation");
    try {
      const rows = await api.deliveryReconciliation();
      box.append(
        el(
          "p",
          rows.length
            ? uiText("用户收件反馈独立记录；协议未知状态与自动重发屏障保持。")
            : uiText("没有待核对的未知送达。"),
        ),
      );
      for (const row of rows) {
        const card = el("article");
        card.append(
          el(
            "p",
            phrase("{p0} · {p1} · {p2} · 用户反馈：{p3}", {
              p0: row.platform,
              p1: row.delivery_id,
              p2: uiText(row.state),
              p3: row.user_feedback || "未记录",
            }),
          ),
        );
        for (const [value, title] of [
          ["received", "我已收到"],
          ["not_received", "我未收到"],
          ["unsure", "暂不能确定"],
        ]) {
          const button = el("button", uiText(title));
          button.type = "button";
          button.addEventListener("click", async () => {
            try {
              await api.deliveryFeedback({
                ledger: row.ledger,
                delivery_id: row.delivery_id,
                outcome: value,
              });
              notify("用户反馈已记录；未改变协议确认或重发额度。");
            } catch (error) {
              notify(error.message, true);
            }
          });
          card.append(button);
        }
        box.append(card);
      }
      notify("");
    } catch (error) {
      notify(error.message, true);
    }
  });
  const countdown = createBatchCountdown(onUpdate, (error) => {
    captureMessage("batches")(error.message, true);
  });
  const progressRefresh = createBatchProgressRefresh(onUpdate, (error) => {
    captureMessage("batches")(error.message, true);
  });
  return {
    render(items) {
      const staging = el("div");
      renderPendingBatches(items, api, onUpdate, staging);
      const next = new Map();
      const nodes = Array.from(staging.children).map((card, index) => {
        const batch = items[index];
        if (!batch) return card;
        const signature = JSON.stringify(batch),
          previous = records.get(batch.id);
        if (
          previous &&
          previous.signature !== signature &&
          hasUnsavedChanges(previous.card)
        ) {
          const draft = previous.card.querySelector(".batch-question");
          if (draft) {
            card.querySelector(".batch-question")?.remove();
            if (!batch.needs_question) {
              draft.dataset.questionFixed = "true";
              draft.querySelector('[type="submit"]').disabled = true;
              card.append(
                el(
                  "p",
                  uiText(
                    "原问题已固定；下面的新编辑尚未提交，刷新或离开前请核对。",
                  ),
                  "muted",
                ),
              );
            }
            card.append(draft);
          }
        }
        const stable = previous?.signature === signature ? previous.card : card;
        next.set(batch.id, { signature, card: stable });
        return stable;
      });
      reconcileChildren($("batch-list"), nodes);
      records.clear();
      for (const [id, record] of next) records.set(id, record);
      countdown.update(items);
      progressRefresh.update(items);
    },
    setVisible(value) {
      countdown.setVisible(value);
      progressRefresh.setVisible(value);
    },
    clear() {
      countdown.clear();
      progressRefresh.clear();
      records.clear();
      $("batch-list").replaceChildren();
    },
  };
}
