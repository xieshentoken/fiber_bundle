import { phrase, textNode, uiText } from "./i18n/index.js";
import { videoSkillSettings } from "./video_skill_settings.js";
import { mountModelTabs } from "./model_tabs.js";
// Owned task controls; model execution and storage stay behind the local API.
import { $, action, el, message, captureMessage } from "./ui.js";
import { captureDraftSave } from "./unsaved_changes.js";
import { createPolicyEditor } from "./models_policy.js";
import { createModelCatalog } from "./models_catalog.js";

const states = {
  queued: "待执行",
  reserved: "已入队",
  running: "执行中",
  succeeded: "完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  waiting_budget: "预算不足",
};
export function createModelsView(api) {
  mountModelTabs($("models-panel"));
  const policy = createPolicyEditor(api);
  const catalog = createModelCatalog(api);
  const extractionSettings = videoSkillSettings(api);
  $("models-catalog")?.after(extractionSettings);
  let epoch = 0,
    tasks = [],
    jobs = [];
  function button(label, work) {
    const node = el("button", uiText(label));
    node.type = "button";
    action(node, work);
    return node;
  }
  function render() {
    const container = $("query-task-list");
    container.replaceChildren();
    if (!tasks.length) {
      container.append(el("p", uiText("暂无模型查询任务。")));
      return;
    }
    for (const task of tasks) {
      const card = el("article", null, "row");
      card.append(
        el(
          "h3",
          uiText(
            task.client_name +
              " · " +
              (task.role === "local" ? "本地查询" : "云端整理"),
          ),
        ),
        el("p", states[task.state] || task.state),
        el("p", task.task_id, "muted"),
      );
      controlsDetail(card, task);
      if (task.error_code) card.append(el("p", task.error_code, "error"));
      const job = jobs.find(
        (j) => j.role === task.role && j.task_id === task.task_id,
      );
      if (job)
        card.append(
          el(
            "p",
            phrase("有限执行：{p0}{p1}", {
              p0: states[job.state] || job.state,
              p1: job.error_code ? " · " + job.error_code : "",
            }),
            "muted",
          ),
        );
      const controls = el("div", null, "query-controls");
      if (task.state === "queued" && !job) {
        const consent = el("label", null, "check"),
          check = el("input");
        check.type = "checkbox";
        consent.append(check, textNode(uiText("批准此任务的一次模型调用")));
        controls.append(consent);
        controls.append(
          button("启动一次", async () => {
            if (!check.checked) throw new Error("请先批准此次模型调用。");
            await api.launchQueryJob({
              role: task.role,
              client_id: task.client_id,
              task_id: task.task_id,
              cloud_edit: false,
            });
            await refresh();
          }),
        );
      }
      if (task.state === "queued" && !job)
        controls.append(
          button("取消待执行任务", async () => {
            await api.cancelQueryTask(task.role, task.task_id);
            await refresh();
          }),
        );
      if (job && ["reserved", "running"].includes(job.state)) {
        controls.append(
          button("取消任务", async () => {
            await api.cancelQueryJob(job.id);
            await refresh();
          }),
        );
        controls.append(
          button("核对中断状态", async () => {
            await api.recoverQueryJob(job.id);
            await refresh();
          }),
        );
      }
      if (task.state === "succeeded") {
        controls.append(
          button("查看答案与引文", async () => {
            const current = epoch,
              result = await api.queryResult(task.role, task.task_id);
            if (current !== epoch) return;
            const out = el("div", null, "query-result");
            if (result.cloud_answer)
              out.append(
                el("p", uiText("云端未核验草稿")),
                el("pre", result.cloud_answer.text),
              );
            for (const quote of result.quotations || [])
              out.append(el("blockquote", quote.quote));
            for (const claim of result.claims || [])
              out.append(el("p", claim.text));
            for (const caution of result.cautions || [])
              out.append(el("p", caution, "muted"));
            card.querySelector(".query-result")?.remove();
            card.append(out);
          }),
        );
        if (task.role === "local")
          controls.append(
            button("准备云端整理", async () => {
              await api.submitCloudQuery({
                client_id: task.client_id,
                local_task_id: task.task_id,
                request_id: window.crypto.randomUUID(),
                configuration_sha256: task.configuration_sha256,
              });
              await refresh();
            }),
          );
      }
      if (task.role === "cloud" && task.state === "queued" && !job) {
        const index = el("select"),
          label = el("label", uiText("批准配置中的备用模型"));
        label.append(index);
        controls.append(label);
        controls.append(
          button("读取此任务的备用列表", async () => {
            const current = epoch,
              detail = await api.queryDetail("cloud", task.task_id);
            if (current !== epoch) return;
            index.replaceChildren();
            for (const choice of detail.backup_choices) {
              const option = el(
                "option",
                phrase("{p0} · 任务上限 {p1} nano USD", {
                  p0: choice.model,
                  p1: choice.task_limit_nusd,
                }),
              );
              option.value = choice.index;
              index.append(option);
            }
            if (!detail.backup_choices.length)
              message("此任务配置没有声明备用模型。", true, "models");
          }),
        );
        const consent = el("label", null, "check"),
          check = el("input");
        check.type = "checkbox";
        consent.append(check, textNode(uiText("明确批准声明中的此备用模型")));
        controls.append(consent);
        controls.append(
          button("批准备用选择", async () => {
            if (!check.checked || !index.value)
              throw new Error("请读取备用列表并明确批准此次选择。");
            await api.selectQueryBackup(task.task_id, {
              client_id: task.client_id,
              backup_index: Number(index.value),
              selection_id: window.crypto.randomUUID(),
              approve: true,
            });
            await refresh();
          }),
        );
      }
      card.append(controls);
      container.append(card);
    }
  }
  function controlsDetail(card, task) {
    card.append(
      button("查看调用费用与审计", async () => {
        const current = epoch,
          detail = await api.queryDetail(task.role, task.task_id);
        if (current !== epoch) return;
        const audit = el("div", null, "query-audit");
        if (detail.cloud_call) {
          const call = detail.cloud_call;
          audit.append(
            el(
              "p",
              phrase("费用状态：{p0}；预留 {p1} nano USD；结算 {p2} nano USD", {
                p0: uiText(call.state),
                p1: call.reserved_nusd,
                p2: call.charged_nusd ?? "未知",
              }),
            ),
          );
        }
        if (detail.local_call)
          audit.append(
            el(
              "p",
              phrase("本地调用：{p0}；输入 {p1} / 输出 {p2} token", {
                p0: uiText(detail.local_call.state),
                p1: detail.local_call.input_tokens ?? "未知",
                p2: detail.local_call.output_tokens ?? "未知",
              }),
            ),
          );
        for (const selection of detail.selections || [])
          audit.append(
            el(
              "p",
              phrase("备用选择 {p0}：{p1} {p2}", {
                p0: selection.backup_index,
                p1: uiText(selection.state),
                p2: selection.reason || "",
              }),
            ),
          );
        if (!detail.local_call && !detail.cloud_call)
          audit.append(el("p", uiText("尚无模型调用记录。")));
        card.querySelector(".query-audit")?.remove();
        card.append(audit);
      }),
    );
  }
  async function refresh() {
    const current = ++epoch;
    const [data, clients] = await Promise.all([
      api.queryTasks(),
      api.clients(),
    ]);
    if (current !== epoch) return;
    tasks = data.tasks;
    jobs = data.jobs;
    const select = $("query-client");
    const selected = select.value;
    select.replaceChildren();
    for (const client of clients.filter((c) => c.active)) {
      const opt = el("option", client.name);
      opt.value = client.id;
      select.append(opt);
    }
    if ([...select.options].some((o) => o.value === selected))
      select.value = selected;
    render();
  }
  action($("query-refresh"), async () => {
    await catalog.refresh();
    await policy.refresh();
    await refresh();
  });
  $("query-submit").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!policy.digest) {
      message("请先保存模型配置。", true, "models");
      return;
    }
    const notify = captureMessage("models"),
      acknowledge = captureDraftSave(event.target),
      button = event.submitter;
    button.disabled = true;
    try {
      await api.submitQuery({
        client_id: $("query-client").value,
        request_id: window.crypto.randomUUID(),
        configuration_sha256: policy.digest,
        question: $("query-question").value,
        retrieval_query: $("query-search").value,
        limit: 10,
        mode: "extractive",
      });
      acknowledge();
      await refresh();
      notify("查询已入队，请在任务卡片明确启动。");
    } catch (error) {
      notify(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  return {
    clear() {
      epoch++;
      tasks = [];
      jobs = [];
      policy.clear();
      catalog.clear();
      extractionSettings.dispatchEvent(new window.Event("fiber:clear"));
      $("query-task-list").replaceChildren();
      $("query-client").replaceChildren();
      $("query-question").value = "";
      $("query-search").value = "";
    },
    get hasUnsavedChanges() {
      return policy.hasUnsavedChanges || catalog.dirty;
    },
    async refresh() {
      await catalog.refresh();
      await policy.refresh();
      await refresh();
    },
  };
}
