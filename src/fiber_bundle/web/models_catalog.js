import { joinText, phrase, setText, translate, uiText } from "./i18n/index.js";
// Saved model controls; provider calls and trusted runtime operations stay behind the API.
import { $, el, message } from "./ui.js";
import { createModelWizard, EFFORT_LABELS } from "./model_wizard.js";
import { modelBudgetSettings } from "./model_budget_settings.js";

export function createModelCatalog(api) {
  const root = $("models-catalog"),
    list = el("div", null, "model-list"),
    status = el("p", null, "muted"),
    environment = el("div", null, "model-environment");
  const add = el("button", uiText("添加模型")),
    refreshButton = el("button", uiText("刷新模型列表"));
  add.id = "models-add";
  add.disabled = true;
  add.setAttribute("aria-haspopup", "dialog");
  add.type = refreshButton.type = "button";
  const toolbar = el("div", null, "panel-actions");
  toolbar.append(add, refreshButton);
  const budgets = modelBudgetSettings(api);
  let catalog,
    epoch = 0,
    timer;
  const tests = new Map();
  const wizard = createModelWizard(api.modelsApi, async () => {
    budgets.clear();
    await refresh();
    message("模型已保存；保存不触发调用。", false, "models");
  });
  root.append(toolbar, status, list, environment, budgets.root, wizard.dialog);
  function button(label, work, parent) {
    const node = el("button", uiText(label));
    node.type = "button";
    node.addEventListener("click", async () => {
      node.disabled = true;
      try {
        await work(node);
      } catch (error) {
        message(error.message, true, "models");
      } finally {
        node.disabled = false;
      }
    });
    parent.append(node);
    return node;
  }
  function render() {
    list.replaceChildren();
    const models = catalog.models.filter((m) => !m.deleted),
      keys = new Map(catalog.credentials.map((k) => [k.id, k]));
    setText(
      status,
      models.length
        ? uiText("模型已保存；调用是否可用以对应验证结果为准。")
        : uiText("尚未添加模型。可直接输入 API Key，无需配置文件或镜像摘要。"),
    );
    for (const item of models) {
      const key = keys.get(item.credential_id),
        card = el("article", null, "model-card"),
        header = el("div", null, "model-card-heading");
      header.append(el("h3", item.model));
      if (catalog.default.configuration_id === item.id)
        header.append(el("span", uiText("应用默认"), "status-badge"));
      card.append(
        header,
        el(
          "p",
          joinText([
            "",
            key.name,
            " · ",
            key.provider,
            " · ",
            key.provider === "local" ? uiText("本地") : uiText("云端"),
            " · ",
            EFFORT_LABELS[item.effort] || item.effort,
            "",
          ]),
        ),
      );
      const verification =
        item.verification?.state === "passed"
          ? "调用验证通过（当前配置）"
          : "已保存 · 调用未验证";
      card.append(
        el("p", item.active ? uiText(verification) : uiText("已停用"), "muted"),
      );
      const references = item.references || [],
        controls = el("div", null, "panel-actions");
      card.append(
        el(
          "p",
          references.length
            ? phrase("关联：{p0}", {
                p0: references.map((r) => r.name).join("、"),
              })
            : uiText("尚无默认项或固定 Agent 引用"),
          "muted",
        ),
      );
      const audioOnly =
        references.some((r) => r.kind === "asr") ||
        item.capabilities?.compatible === false;
      if (audioOnly)
        card.append(
          el(
            "p",
            uiText(
              "此配置用于音频转写或其他专用接口；请在实际任务中核对，文本测试不适用。",
            ),
            "muted",
          ),
        );
      if (
        item.active &&
        !audioOnly &&
        catalog.default.configuration_id !== item.id
      )
        button(
          "设为默认",
          async () => {
            await api.modelsApi.control(item.id, {
              action: "default",
              expected_revision: item.revision,
              expected_default_revision: catalog.default.revision,
            });
            await refresh();
            message("默认模型已更新；已有任务保留原配置。", false, "models");
          },
          controls,
        );
      button("编辑", (node) => wizard.open(catalog, item, node), controls);
      if (item.active && !audioOnly)
        button(
          "测试调用（消耗 Tokens）",
          async () => {
            const operation = await api.modelsApi.test(item.id, item.revision);
            tests.set(operation.id, item.id);
            card.querySelector(".model-test-state")?.remove();
            card.append(
              el(
                "p",
                uiText("测试已提交；不发送知识库内容。"),
                "model-test-state",
              ),
            );
            schedule();
          },
          controls,
        );
      button(
        item.active ? "停用" : "启用",
        async () => {
          await api.modelsApi.control(item.id, {
            action: item.active ? "disable" : "enable",
            expected_revision: item.revision,
          });
          await refresh();
        },
        controls,
      );
      button(
        "删除",
        async () => {
          if (references.length)
            throw new Error(
              "请先替换这些引用：" +
                references.map((r) => r.name).join("、") +
                "。Agent 的绑定在 Agent 与客户端中修改。",
            );
          if (
            !window.confirm(
              translate(`删除模型 ${item.model}？历史调用与费用会保留。`),
            )
          )
            return;
          await api.modelsApi.control(item.id, {
            action: "delete",
            expected_revision: item.revision,
          });
          await refresh();
        },
        controls,
      );
      card.dataset.modelId = item.id;
      card.append(controls);
      list.append(card);
    }
  }
  function schedule() {
    clearTimeout(timer);
    const current = epoch;
    timer = setTimeout(async () => {
      if (current !== epoch) return;
      let changed = false;
      for (const [id, modelId] of tests) {
        try {
          const result = await api.modelsApi.testStatus(id);
          if (current !== epoch) return;
          if (!["queued", "running"].includes(result.state)) {
            tests.delete(id);
            changed = true;
            message(
              result.state === "succeeded"
                ? "模型测试调用通过；Tokens 已记账，价格未核实时费用未知。"
                : result.message ||
                    `测试未完成（${result.error_code || result.state}）；请核对该模型设置。`,
              result.state !== "succeeded",
              "models",
            );
            const card = [...list.children].find(
              (n) => n.dataset.modelId === modelId,
            );
            if (card)
              card.append(
                el("p", result.message || result.state, "model-test-state"),
              );
          }
        } catch (error) {
          tests.delete(id);
          message(error.message, true, "models");
        }
      }
      if (changed) await refresh();
      if (tests.size) schedule();
    }, 1000);
  }
  async function refreshEnvironment() {
    const current = epoch;
    environment.replaceChildren(el("p", uiText("正在核验受限运行环境…")));
    let state;
    try {
      state = await api.modelsApi.runtime();
    } catch (error) {
      state = { state: "unavailable", message: error.message };
    }
    if (current !== epoch) return;
    environment.replaceChildren(
      el("h3", uiText("受限运行环境")),
      el(
        "p",
        state.state === "ready"
          ? phrase("Pi {p0} · {p1} · 已就绪", {
              p0: state.pi_version,
              p1: state.architecture,
            })
          : uiText(state.message || "环境未就绪"),
      ),
    );
    const detail = el("details");
    detail.append(
      el("summary", uiText("环境诊断")),
      el(
        "p",
        uiText(
          state.image || state.expected_image || "请先启动 Docker 后重新检查。",
        ),
        "model-runtime-digest",
      ),
    );
    environment.append(detail);
    const controls = el("div", null, "panel-actions");
    environment.append(controls);
    button("重新检查", refreshEnvironment, controls);
    if (state.state !== "ready")
      button(
        "准备运行环境",
        async () => {
          await api.modelsApi.prepare();
          message(
            "开始准备应用附带的已核实 Pi 环境；不会安装 Docker 或启动模型任务。",
            false,
            "models",
          );
          pollEnvironment(current, Date.now() + 180000);
        },
        controls,
      );
  }
  async function pollEnvironment(current, deadline) {
    if (current !== epoch || Date.now() > deadline) return;
    try {
      const state = await api.modelsApi.runtime();
      if (current !== epoch) return;
      if (!["queued", "running"].includes(state.preparation?.state)) {
        await refreshEnvironment();
        return;
      }
    } catch (error) {
      message(error.message, true, "models");
      return;
    }
    setTimeout(() => pollEnvironment(current, deadline), 1500);
  }
  async function refresh() {
    const current = epoch;
    const result = await api.modelsApi.catalog();
    if (current !== epoch) return;
    catalog = result;
    add.disabled = false;
    render();
    await refreshEnvironment();
  }
  add.addEventListener("click", () => {
    if (catalog) wizard.open(catalog, null, add);
  });
  refreshButton.addEventListener("click", () =>
    refresh().catch((e) => message(e.message, true, "models")),
  );
  return {
    refresh,
    get dirty() {
      return wizard.dirty;
    },
    clear() {
      epoch++;
      clearTimeout(timer);
      tests.clear();
      wizard.close(true);
      list.replaceChildren();
      environment.replaceChildren();
      budgets.clear();
      catalog = null;
      add.disabled = true;
    },
  };
}
