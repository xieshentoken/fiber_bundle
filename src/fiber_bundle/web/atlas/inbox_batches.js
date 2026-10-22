// Message batches: choose what to do with a frozen batch. Choosing is not publishing,
// and unknown delivery outcomes are reconciled, never re-sent.
import { h, clear, icon } from "./dom.js";
import { createInboxHistory } from "./inbox_history.js";
import { isInboxComplete } from "../inbox_status.js";
import { strings } from "./i18n.js";
import {
  badge,
  empty,
  facts,
  failure,
  field,
  input,
  notice,
  skeleton,
  disclosure,
  button,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import { createBatchCountdown } from "../batch_countdown.js";
import {
  hasUnsavedChanges,
  captureDraftSave,
  rememberDraft,
} from "../unsaved_changes.js";
import { batchTitle } from "./home_model.js";
import { dateTime, timeOnly, bytes, number } from "./format.js";

const t = strings({
  zh: {
    intro: "默认显示未完成或需核对的批次；已完成记录保留在历史中。",
    none: "暂无待处理批次；可勾选“显示已完成记录”查看历史。",
    finished: "笔记已保存，处理已完成",
    savedNeedsAttention: "笔记已保存，后续处理待核对",
    stored: "原件已保存；笔记需另行审核发布",
    chosen: "操作已选择；等待处理",
    buffering: "等待批次冻结",
    question: "待补充咨询问题",
    ready: "待确认",
    incomplete: "内容待补齐",
    received: "收到于 {time}",
    freezeAt: "预计 {time} 冻结（以后台状态为准）",
    freezeDue: "缓冲时间已到，等待后台确认冻结。",
    files: "已取得文件",
    members: "ZIP 成员",
    integrity: "原聊天消息数与完整性未知。",
    route: "身份 / 知识库",
    task: "来源任务",
    publication: "发布",
    quota: "等待明确的 Store 处理额度；选择操作不会自动授予额度。",
    receiver: "接收器状态不可用，当前无法确认处理进度。",
    zero: "Store 剩余额度为零，原件保存尚未执行。",
    attachments: "附件结构",
    missing: "附件 {state}，缺件 {n}",
    unknown: "未知",
    saveOne: "保存为一份",
    saveSeparately: "分别保存",
    consult: "仅用于咨询",
    cancel: "取消批次",
    questionLabel: "咨询问题",
    submitQuestion: "补充问题",
    cancelTitle: "取消本批次？",
    cancelBody: "原件会保留。",
    keep: "保留批次",
    cancelled: "批次已取消；原件仍保留。",
    choiceDone: "选择已记录；资料尚未入库，咨询尚未执行。",
    questionDone: "咨询问题已记录；尚未交给模型处理。",
    refreshFailed: "选择已记录，但列表刷新失败，请重新刷新。",
    delivery: "未知送达核对",
    deliveryIntro: "用户收件反馈独立记录；协议未知状态与自动重发屏障保持。",
    deliveryLoad: "读取待核对送达",
    deliveryNone: "没有待核对的未知送达。",
    feedback: "用户反馈：{v}",
    notRecorded: "未记录",
    gotIt: "我已收到",
    notReceived: "我未收到",
    unsure: "暂不能确定",
    feedbackDone: "用户反馈已记录；未改变协议确认或重发额度。",
    feishu: "飞书",
    weixin: "微信",
  },
  en: {
    intro:
      "Pending or unresolved batches are shown by default. Completed records remain in history.",
    none: "No pending batches. Select Show completed history to view past records.",
    finished: "Notes saved; processing complete",
    savedNeedsAttention: "Notes saved; follow-up needs review",
    stored: "Originals saved; notes still need review and publishing",
    chosen: "Action chosen; waiting to process",
    buffering: "Waiting for the batch to freeze",
    question: "Consultation question needed",
    ready: "Ready to choose",
    incomplete: "Content still arriving",
    received: "Received {time}",
    freezeAt: "Expected to freeze at {time} (backend state decides)",
    freezeDue: "Buffer time is up; waiting for the backend to freeze it.",
    files: "Files received",
    members: "ZIP members",
    integrity: "The original chat message count and completeness are unknown.",
    route: "Identity / vault",
    task: "Source task",
    publication: "Publication",
    quota:
      "Waiting for an explicit Store quota; choosing an action does not grant quota.",
    receiver: "Receiver status unavailable; progress can't be confirmed.",
    zero: "Store quota is zero; originals have not been saved.",
    attachments: "Attachment structure",
    missing: "Attachment {state}, missing {n}",
    unknown: "unknown",
    saveOne: "Save as one",
    saveSeparately: "Save separately",
    consult: "Consult only",
    cancel: "Cancel batch",
    questionLabel: "Consultation question",
    submitQuestion: "Add question",
    cancelTitle: "Cancel this batch?",
    cancelBody: "Originals are kept.",
    keep: "Keep batch",
    cancelled: "Batch cancelled; originals kept.",
    choiceDone: "Choice recorded. Nothing has been filed or consulted yet.",
    questionDone: "Question recorded; not sent to a model yet.",
    refreshFailed:
      "Choice recorded, but the list didn't refresh. Please refresh.",
    delivery: "Unknown delivery reconciliation",
    deliveryIntro:
      "User receipt feedback is recorded separately; unknown protocol states and the no-resend barrier stay in place.",
    deliveryLoad: "Load deliveries to reconcile",
    deliveryNone: "No unknown deliveries to reconcile.",
    feedback: "User feedback: {v}",
    notRecorded: "not recorded",
    gotIt: "I received it",
    notReceived: "I didn't receive it",
    unsure: "Not sure yet",
    feedbackDone:
      "Feedback recorded; protocol confirmation and resend quota unchanged.",
    feishu: "Feishu",
    weixin: "WeChat",
  },
});
function statusOf(batch) {
  if (isInboxComplete(batch)) return [t("finished"), "success"];
  if (batch.publication_state === "published")
    return [t("savedNeedsAttention"), "waiting"];
  if (batch.store_state === "stored") return [t("stored"), "success"];
  if (batch.state === "selected" && !batch.needs_question)
    return [t("chosen"), "info"];
  if (batch.state === "buffering") return [t("buffering"), "info"];
  if (batch.needs_question) return [t("question"), "waiting"];
  return batch.content_ready
    ? [t("ready"), "waiting"]
    : [t("incomplete"), "waiting"];
}
export function choiceDisabled(batch, choice) {
  return (
    (batch.state !== "frozen" &&
      !(choice === "cancel" && batch.state === "selected")) ||
    (choice !== "cancel" && !batch.content_ready)
  );
}

export function create(ctx, params) {
  const list = h("div.a-stack");
  const history = createInboxHistory(() => {
    if (ctx.confirmLeave && !ctx.confirmLeave()) return false;
    void refresh();
  }, params.id);
  const root = h(
    "div.a-stack",
    notice(t("intro")),
    history.node,
    list,
    deliveryCard(),
  );
  let generation = 0,
    poll = null;
  const countdown = createBatchCountdown(
    () => refresh(true),
    (error) => toast(error.message, { tone: "error" }),
  );
  async function afterChoice(text) {
    try {
      await refresh();
      toast(text, { tone: "success" });
    } catch {
      toast(t("refreshFailed"), { tone: "warning" });
    }
  }
  async function cancelBatch(batch) {
    const ok = await confirm({
      title: t("cancelTitle"),
      body: t("cancelBody"),
      confirmLabel: t("cancel"),
      cancelLabel: t("keep"),
      tone: "danger",
    });
    if (!ok) return false;
    await ctx.api.chooseBatch(batch.id, "cancel");
    await afterChoice(t("cancelled"));
  }
  function batchCard(batch) {
    const [status, tone] = statusOf(batch);
    const node = h("article.a-card.a-batch", { dataset: { id: batch.id } });
    const head = h(
      "header.a-card-head",
      h(
        "div.a-card-titles",
        h("h2.a-card-title", batchTitle(batch)),
        h(
          "p.a-card-subtitle",
          t("received", { time: dateTime(batch.received_at) }),
        ),
      ),
      badge(status, tone),
    );
    node.append(head);
    if (batch.state === "buffering" && Number.isFinite(batch.freeze_at)) {
      const left = batch.freeze_at * 1000 - Date.now();
      if (left > 0) {
        const bar = h("i");
        bar.style.animationDuration = `${left}ms`;
        node.append(
          h("div.a-timer", { "aria-hidden": "true" }, bar),
          h("p.a-subtle", t("freezeAt", { time: timeOnly(batch.freeze_at) })),
        );
      } else node.append(h("p.a-subtle", t("freezeDue")));
    }
    if (batch.preview) node.append(h("blockquote.a-quote", batch.preview));
    node.append(
      facts([
        batch.platform
          ? [
              t("route"),
              `${t(batch.platform) || batch.platform} · ${batch.client_name || batch.client_id} / ${batch.vault_name || batch.vault_id}`,
            ]
          : null,
        batch.received_file_count !== undefined
          ? [t("files"), number(batch.received_file_count)]
          : null,
        batch.archive_member_count !== undefined
          ? [t("members"), number(batch.archive_member_count)]
          : null,
        batch.processing_state ? [t("task"), batch.processing_state] : null,
        batch.publication_state
          ? [t("publication"), batch.publication_state]
          : null,
      ]),
    );
    if (batch.received_file_count !== undefined)
      node.append(h("p.a-subtle", t("integrity")));
    const blockers = {
      requires_explicit_store_quota: ["quota", "info"],
      receiver_unavailable: ["receiver", "error"],
      store_quota_zero: ["zero", "warning"],
    };
    if (blockers[batch.processing_blocker])
      node.append(
        notice(
          t(blockers[batch.processing_blocker][0]),
          blockers[batch.processing_blocker][1],
        ),
      );
    if (batch.attachments?.length)
      node.append(
        disclosure(
          t("attachments"),
          h(
            "ul.a-plain-list",
            batch.attachments.map((item) =>
              h(
                "li",
                item.name
                  ? `${item.name} · ${item.media_type} · ${bytes(item.bytes)}`
                  : t("missing", {
                      state: item.state,
                      n: item.missing_files ?? t("unknown"),
                    }),
              ),
            ),
          ),
        ),
      );
    if (!isInboxComplete(batch))
      node.append(batch.needs_question ? questionForm(batch) : choices(batch));
    return node;
  }
  function choices(batch) {
    const bar = h("div.a-form-actions");
    for (const [choice, variant] of [
      ["save_one", "primary"],
      ["save_separately", null],
      ["consult", null],
      ["cancel", "danger-outline"],
    ]) {
      const label = {
        save_one: t("saveOne"),
        save_separately: t("saveSeparately"),
        consult: t("consult"),
        cancel: t("cancel"),
      }[choice];
      const control = button(label, {
        variant,
        iconName: choice === "cancel" ? "close" : null,
      });
      control.disabled = choiceDisabled(batch, choice);
      control.addEventListener("click", () =>
        run(control, async () => {
          if (choice === "cancel") return cancelBatch(batch);
          for (const other of bar.querySelectorAll("button"))
            other.disabled = true;
          try {
            await ctx.api.chooseBatch(batch.id, choice);
          } catch (error) {
            for (const other of bar.querySelectorAll("button"))
              other.disabled = choiceDisabled(batch, other.dataset.choice);
            throw error;
          }
          await afterChoice(t("choiceDone"));
        }),
      );
      control.dataset.choice = choice;
      bar.append(control);
    }
    return bar;
  }
  function questionForm(batch) {
    const question = input({
      id: "batch-question-" + batch.id,
      name: "question",
      required: true,
      maxLength: 4000,
    });
    const submit = h(
      "button.a-button.is-primary",
      { type: "submit" },
      t("submitQuestion"),
    );
    const cancel = button(t("cancel"), { variant: "danger-outline" });
    const form = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      field(t("questionLabel"), question),
      h("div.a-form-actions", submit, cancel),
    );
    rememberDraft(form);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(submit, async () => {
        const acknowledge = captureDraftSave(form);
        await ctx.api.chooseBatch(batch.id, "consult", question.value);
        acknowledge();
        await afterChoice(t("questionDone"));
      });
    });
    cancel.addEventListener("click", () =>
      run(cancel, () => cancelBatch(batch)),
    );
    return form;
  }
  function deliveryCard() {
    const box = h("div.a-stack");
    const load = button(t("deliveryLoad"), { iconName: "refresh" });
    load.addEventListener("click", () =>
      run(load, async () => {
        const rows = await ctx.api.deliveryReconciliation();
        clear(
          box,
          h("p.a-subtle", rows.length ? t("deliveryIntro") : t("deliveryNone")),
          rows.map((row) =>
            h(
              "div.a-delivery",
              h(
                "p",
                h("strong", `${row.platform} · ${row.delivery_id}`),
                " · ",
                row.state,
                " · ",
                t("feedback", { v: row.user_feedback || t("notRecorded") }),
              ),
              h(
                "div.a-form-actions",
                [
                  ["received", t("gotIt")],
                  ["not_received", t("notReceived")],
                  ["unsure", t("unsure")],
                ].map(([outcome, label]) => {
                  const control = button(label, { variant: "small" });
                  control.addEventListener("click", () =>
                    run(control, async () => {
                      await ctx.api.deliveryFeedback({
                        ledger: row.ledger,
                        delivery_id: row.delivery_id,
                        outcome,
                      });
                      toast(t("feedbackDone"), { tone: "success" });
                    }),
                  );
                  return control;
                }),
              ),
            ),
          ),
        );
      }),
    );
    return disclosure(h("span", icon("pulse"), " ", t("delivery")), load, box);
  }
  async function refresh(quiet = false) {
    const mine = ++generation;
    if (quiet && hasUnsavedChanges(list)) return;
    if (!list.childElementCount) clear(list, skeleton(3));
    let batches;
    try {
      batches = await ctx.api.pendingBatches();
    } catch (error) {
      if (mine === generation && !quiet)
        clear(
          list,
          failure(error, () => refresh()),
        );
      return;
    }
    if (mine !== generation) return;
    const shown = history.filter(batches);
    clear(
      list,
      shown.length
        ? shown.map(batchCard)
        : empty(t("none"), { iconName: "batch" }),
    );
    rememberDraft(list);
    countdown.update(shown);
    if (params.id)
      list
        .querySelector(`[data-id="${CSS.escape(params.id)}"]`)
        ?.scrollIntoView({ block: "center" });
  }
  countdown.setVisible(true);
  poll = setInterval(() => {
    if (!document.hidden) void refresh(true);
  }, 10000);
  return {
    node: root,
    refresh: () => refresh(false),
    dispose() {
      generation++;
      clearInterval(poll);
      countdown.clear();
    },
  };
}
