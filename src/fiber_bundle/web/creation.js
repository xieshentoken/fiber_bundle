import { phrase, translate, uiText } from "./i18n/index.js";
// Draft management reuses immutable intent/revision contracts. Execution remains explicitly opt-in.
import { $, action, el, checkbox } from "./ui.js";
import { control, showJSON } from "./record_ui.js";
import {
  rememberDraft,
  captureDraftSave,
  hasUnsavedChanges,
} from "./unsaved_changes.js";
import { confirmAction } from "./confirm_dialog.js";
export function createCreationView(api) {
  let epoch = 0,
    clients = [],
    queries = [],
    vaults = [],
    formScope = null;
  function select(items, label) {
    const box = el("label", uiText(label)),
      input = el("select");
    for (const item of items) {
      const opt = el("option", item.name || item.task_id || item.id);
      opt.value = item.id ?? item.task_id;
      input.append(opt);
    }
    box.append(input);
    return { box, input };
  }
  async function refresh() {
    if (
      hasUnsavedChanges($("creation-content")) &&
      !window.confirm(translate("刷新会丢弃未保存的任务修改，继续？"))
    )
      return false;
    const revision = ++epoch;
    const [data, c, q, v] = await Promise.all([
      api.creationTasks(),
      api.clients(),
      api.queryTasks(),
      api.vaults(),
    ]);
    if (revision !== epoch) return;
    clients = c.filter((c) => c.active);
    queries = q.tasks.filter(
      (t) => t.role === "local" && ["queued", "succeeded"].includes(t.state),
    );
    vaults = v.filter((v) => v.status === "active");
    const nextFormScope = JSON.stringify([clients, queries]);
    if (nextFormScope !== formScope && !hasUnsavedChanges($("creation-form"))) {
      formScope = nextFormScope;
      const form = $("creation-form");
      form.dataset.draft = "tracked";
      form.replaceChildren();
      if (!queries.length) {
        form.append(
          el("p", uiText("固定本地查询资料后，可在此准备文档任务。")),
        );
      } else {
        const identity = select(clients, "任务身份"),
          input = select(queries, "固定查询输入"),
          format = select(
            [
              { id: "md", name: uiText("Markdown 草稿") },
              { id: "txt", name: uiText("文本草稿") },
            ],
            "格式",
          ),
          requirements = el("textarea"),
          label = el("label", uiText("制作要求"));
        requirements.required = true;
        requirements.maxLength = 16384;
        function matchInputs() {
          input.input.replaceChildren();
          for (const query of queries.filter(
            (q) => q.client_id === identity.input.value,
          )) {
            const option = el("option", query.task_id);
            option.value = query.task_id;
            input.input.append(option);
          }
        }
        identity.input.addEventListener("change", matchInputs);
        matchInputs();
        label.append(requirements);
        form.append(
          identity.box,
          input.box,
          format.box,
          label,
          control("准备文档任务", async (notify) => {
            const acknowledge = captureDraftSave(form);
            await api.submitCreation({
              client_id: identity.input.value,
              query_task_id: input.input.value,
              submission_id: window.crypto.randomUUID(),
              requirements: requirements.value,
              requested_format: format.input.value,
            });
            if (revision !== epoch) return;
            acknowledge();
            await refresh();
            notify("文档要求已准备；没有启动模型或交付。");
          }),
        );
        rememberDraft(form);
      }
    }
    const list = $("creation-content");
    list.replaceChildren();
    if (!data.tasks.length) list.append(el("p", uiText("暂无文档制作任务。")));
    for (const task of data.tasks) {
      const card = el("article", null, "row");
      card.append(
        el("h3", task.title || uiText("文档制作任务")),
        el(
          "p",
          phrase("要求修订 {p0} · {p1}", {
            p0: task.revision,
            p1:
              {
                prepared: "等待执行",
                draft: "草稿",
                queued: "排队中",
                running: "制作中",
                succeeded: "制作完成，保存状态见详情",
                completed: "制作完成，保存状态见详情",
                saved: "已保存",
                failed: "制作失败",
                cancelled: "已取消",
                blocked: "已暂停",
              }[task.state] || "状态见详情",
          }),
        ),
        control("查看版本、输入与交付状态", () => details(card, task.task_id)),
      );
      showJSON(card, { task_id: task.task_id, state: task.state }, "任务信息");
      list.append(card);
    }
  }
  async function details(card, id) {
    const previous = card.querySelector(".creation-details");
    if (
      hasUnsavedChanges(previous) &&
      !window.confirm(translate("补充要求尚未提交，确定重新读取？"))
    )
      return;
    const revision = epoch,
      value = await api.creationStatus(id);
    if (revision !== epoch) return;
    card.querySelector(".creation-details")?.remove();
    const detail = el("div", null, "creation-details");
    showJSON(detail, value);
    const addition = el("textarea"),
      label = el("label", uiText("补充要求"));
    addition.maxLength = 16384;
    addition.dataset.draft = "tracked";
    label.append(addition);
    detail.append(label);
    const updated = select(
      [{ id: "", name: "保留当前输入" }, ...queries],
      "纳入固定查询输入（先预览确认）",
    );
    let updateHash = null;
    updated.input.dataset.draft = "tracked";
    detail.append(
      updated.box,
      control("预览输入变化", async () => {
        if (!updated.input.value) throw new Error("请选择明确的新查询输入。");
        const preview = await api.creationInputUpdate(id, updated.input.value);
        if (revision !== epoch) return;
        showJSON(detail, preview);
        updateHash = { id: updated.input.value, sha: preview.preview_sha256 };
      }),
    );
    detail.append(
      control("确认补充到下一版本", async () => {
        if (
          updated.input.value &&
          (updateHash?.id !== updated.input.value || !updateHash.sha)
        )
          throw new Error("请先预览并核对输入变化。");
        const acknowledge = captureDraftSave(detail);
        await api.appendCreation(id, {
          amendment_id: window.crypto.randomUUID(),
          addition: addition.value,
          expected_revision: value.revision,
          expected_sha256: value.plan_sha256,
          ...(updated.input.value
            ? {
                updated_query_task_id: updated.input.value,
                expected_update_sha256: updateHash.sha,
              }
            : {}),
        });
        if (revision !== epoch) return;
        acknowledge();
        if (!hasUnsavedChanges(detail)) await refresh();
        else Object.assign(value, await api.creationStatus(id));
      }),
      control("取消文档及后续版本", async () => {
        if (
          !(await confirmAction({
            title: "取消此文档及其后续版本？",
            body: "草稿、原件和费用保留。",
            confirmLabel: "取消文档任务",
            cancelLabel: "保留任务",
            tone: "danger",
          }))
        )
          return;
        await api.cancelCreation(id, {
          expected_revision: value.revision,
          expected_sha256: value.plan_sha256,
        });
        await refresh();
      }),
    );
    const artifact = value.execution?.draft_version?.artifact;
    if (artifact) {
      detail.append(control("取回未核验草稿", () => api.downloadCreation(id)));
      const target = select(vaults, "保存目标知识库"),
        consent = checkbox("明确保存为分析产物，非独立证据", false);
      detail.append(
        target.box,
        consent.label,
        control("保存分析产物", async (notify) => {
          if (!consent.input.checked)
            throw new Error("请明确确认保存分析产物。");
          const body = {
            target_vault_id: target.input.value,
            expected_artifact_sha256: artifact.sha256,
            save_analysis: true,
          };
          const result = await api.saveCreation(id, body);
          if (revision !== epoch) return;
          showJSON(detail, result);
          if (result.state === "scope_approval_required") {
            detail.append(
              el(
                "p",
                uiText("目标库将扩大读者范围，请核对精确保存意图后批准。"),
              ),
              control("批准这份范围扩张并保存", async () => {
                if (
                  !(await confirmAction({
                    title: "批准读者范围扩张？",
                    body: [
                      "批准当前 SHA 所绑定的读者范围扩张。",
                      { label: "保存意图 SHA", items: [result.intent_sha256] },
                    ],
                    confirmLabel: "批准并保存",
                  }))
                )
                  return;
                await api.approveCreationSave(id, result.intent_sha256);
                await api.saveCreation(id, body);
                await refresh();
              }),
            );
          } else {
            notify("分析保存状态：" + result.state);
          }
        }),
      );
    }
    card.append(detail);
    rememberDraft(detail);
  }
  action($("creation-refresh"), refresh);
  return {
    refresh,
    clear() {
      epoch++;
      formScope = null;
      clients = [];
      queries = [];
      vaults = [];
      $("creation-form").replaceChildren();
      $("creation-content").replaceChildren();
    },
  };
}
