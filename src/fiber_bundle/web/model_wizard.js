import {
  optionText,
  setText,
  translate,
  uiText,
  phrase,
} from "./i18n/index.js";
// One progressive modal. Keys live only in the password field and submitted request.
import { el } from "./ui.js";

export const EFFORT_LABELS = {
  default: "服务商默认",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "更高",
  max: "最高",
};

export function createModelWizard(api, onSaved) {
  const dialog = el("dialog", null, "model-dialog");
  dialog.id = "model-wizard";
  dialog.setAttribute("aria-labelledby", "model-wizard-title");
  const title = el("h2", uiText("添加模型"));
  title.id = "model-wizard-title";
  const form = el("form");
  form.dataset.draft = "ignore";
  const error = el("p", null, "error");
  error.id = "model-wizard-error";
  error.setAttribute("role", "alert");
  error.hidden = true;
  const status = el(
    "p",
    uiText("选择服务商，再读取此 Key 的模型列表。"),
    "muted",
  );
  status.setAttribute("role", "status");
  const controls = el("div", null, "panel-actions");
  const fields = {};
  function field(label, name, type = "text", root = form) {
    const wrapper = el("label", uiText(label)),
      input = el(type === "select" ? "select" : "input");
    input.name = name;
    input.id = "model-wizard-" + name;
    if (type !== "select") input.type = type;
    wrapper.append(input);
    root.append(wrapper);
    fields[name] = input;
    return input;
  }
  const provider = field("服务商", "provider", "select");
  const credential = field("使用已有 API Key", "credential_id", "select");
  const name = field("Key 自定义名称", "name");
  name.required = true;
  name.maxLength = 100;
  const base = field("API 基础地址", "base_url", "url");
  base.placeholder = "https://example.com/v1";
  const key = field("API Key", "api_key", "password");
  key.autocomplete = "off";
  key.spellcheck = false;
  key.maxLength = 4096;
  const advanced = el("details");
  advanced.append(el("summary", uiText("高级设置")));
  const protocol = field("兼容协议", "protocol", "select", advanced);
  for (const [id, label] of [
    ["openai-completions", "OpenAI Chat 兼容"],
    ["openai-responses", "OpenAI Responses 兼容"],
    ["anthropic-messages", "Anthropic Messages 兼容"],
  ])
    protocol.append(optionText(label, id));
  const budget = field(
    "每日 Tokens 预算（同一 Key 的模型共享）",
    "budget_tokens",
    "number",
    advanced,
  );
  budget.min = "0";
  budget.step = "1";
  budget.max = "1000000000000";
  budget.value = "100000";
  advanced.append(
    el(
      "p",
      uiText(
        "已有 Key 沿用现有预算。可在统一模型预算中修改；价格未核实时费用显示未知。",
      ),
      "muted",
    ),
  );
  form.append(advanced);
  const selected = el("fieldset");
  selected.append(el("legend", uiText("选择模型")));
  const model = field("可用模型", "model", "select", selected);
  const manual = field(
    "手动模型 ID（列表接口不可用时）",
    "manual_model",
    "text",
    selected,
  );
  manual.maxLength = 200;
  manual.closest("label").hidden = true;
  setText(
    manual,
    uiText("准确的模型 ID；不会自动标记调用已验证"),
    "placeholder",
  );
  const effort = field("思考强度 / effort", "effort", "select", selected);
  selected.hidden = true;
  form.append(selected, status, error, controls);
  const next = el("button", uiText("读取可用模型")),
    save = el("button", uiText("保存模型")),
    cancel = el("button", uiText("取消"));
  next.type = cancel.type = "button";
  save.type = "submit";
  save.hidden = true;
  controls.append(next, save, cancel);
  dialog.append(title, form);
  let data,
    profile,
    identity,
    baseline = "",
    trigger,
    epoch = 0,
    controller,
    saving = false,
    loading = false,
    discovered = [];
  function shape() {
    return JSON.stringify([
      provider.value,
      credential.value,
      name.value,
      base.value,
      protocol.value,
      budget.value,
      model.value,
      manual.value,
      effort.value,
      Boolean(key.value),
    ]);
  }
  function dirty() {
    return (
      dialog.open &&
      shape() !== baseline &&
      Boolean(name.value || key.value || model.value || manual.value)
    );
  }
  function options() {
    const item =
      discovered.find((m) => m.id === model.value) ||
      (profile?.model === model.value ? profile : null);
    const levels = item?.efforts || item?.capabilities?.efforts || ["default"];
    const old = effort.value;
    effort.replaceChildren();
    for (const id of levels)
      effort.append(optionText(EFFORT_LABELS[id] || id, id));
    effort.value = levels.includes(old) ? old : "default";
    effort.closest("label").hidden = levels.length === 1;
  }
  function invalidate() {
    epoch++;
    controller?.abort();
    loading = false;
    next.disabled = false;
    discovered = [];
    model.replaceChildren();
    manual.value = "";
    selected.hidden = true;
    save.hidden = true;
    setText(status, uiText("连接信息已变化，请重新读取模型。"));
  }
  function providerChanged() {
    const builtin = data.providers.find((p) => p.id === provider.value);
    base.closest("label").hidden = Boolean(builtin);
    base.required = !builtin;
    protocol.closest("label").hidden = Boolean(builtin);
    if (builtin) {
      base.value = builtin.base_url;
      protocol.value = builtin.protocol;
    }
    key.required = !credential.value && provider.value !== "local";
  }
  function chooseKey() {
    const item = data.credentials.find((k) => k.id === credential.value);
    provider.disabled = Boolean(item);
    base.readOnly = protocol.disabled = Boolean(item);
    key.value = "";
    setText(
      key,
      item
        ? uiText("留空沿用；填写新 Key 则轮换，不清零预算")
        : uiText("仅输入 API Key"),
      "placeholder",
    );
    if (item) {
      provider.value = item.provider;
      name.value = item.name;
      base.value = item.base_url;
      protocol.value = item.protocol;
    }
    budget.disabled = Boolean(item);
    providerChanged();
  }
  function connection() {
    const item = data.credentials.find((k) => k.id === credential.value);
    return {
      provider: provider.value,
      name: name.value.trim(),
      base_url: base.value.trim(),
      protocol: protocol.value,
      api_key: key.value.trim() || null,
      credential_id: item?.id || null,
      credential_revision: item?.revision || 0,
    };
  }
  function showError(problem) {
    setText(error, uiText(problem.message));
    error.hidden = false;
    const input = fields[problem.field];
    if (input && !input.disabled && !input.closest("label")?.hidden)
      input.focus();
  }
  function close(force = false) {
    if (
      !force &&
      (saving ||
        (dirty() && !window.confirm(translate("放弃当前未保存的模型配置？"))))
    )
      return false;
    epoch++;
    controller?.abort();
    key.value = "";
    dialog.close();
    trigger?.setAttribute("aria-expanded", "false");
    (trigger?.isConnected
      ? trigger
      : document.getElementById("models-add")
    )?.focus({ preventScroll: true });
    baseline = "";
    return true;
  }
  cancel.addEventListener("click", () => close());
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  provider.addEventListener("change", () => {
    if (["custom", "local"].includes(provider.value))
      protocol.value = "openai-completions";
    providerChanged();
    invalidate();
  });
  credential.addEventListener("change", () => {
    chooseKey();
    invalidate();
  });
  for (const input of [key, base, protocol])
    input.addEventListener("input", invalidate);
  model.addEventListener("change", () => {
    manual.value = "";
    options();
  });
  manual.addEventListener("input", () => {
    model.value = "";
    options();
  });
  next.addEventListener("click", async () => {
    if (!form.reportValidity() || loading) return;
    const current = ++epoch;
    controller?.abort();
    controller = new AbortController();
    loading = true;
    next.disabled = true;
    error.hidden = true;
    setText(status, uiText("正在读取模型；不会发送知识库资料或调用模型。"));
    try {
      const result = await api.discover(connection(), controller.signal);
      if (current !== epoch || !dialog.open) return;
      discovered = result.models;
      manual.closest("label").hidden = discovered.length > 0;
      model.replaceChildren(optionText("请选择模型", ""));
      for (const item of discovered)
        model.append(new window.Option(item.name || item.id, item.id));
      if (profile && discovered.some((m) => m.id === profile.model))
        model.value = profile.model;
      selected.hidden = save.hidden = false;
      options();
      setText(
        status,
        discovered.length
          ? phrase("已读取 {p0} 个模型；调用权限和服务商余额尚未验证。", {
              p0: discovered.length,
            })
          : uiText("服务返回空列表，可手动填写模型 ID；调用尚未验证。"),
      );
    } catch (problem) {
      if (current !== epoch || !dialog.open || problem.code === "cancelled")
        return;
      selected.hidden = save.hidden = false;
      options();
      showError(problem);
      manual.closest("label").hidden = false;
      setText(
        status,
        uiText("可修正连接后重新读取；服务未提供列表时可手动填写模型 ID。"),
      );
    } finally {
      if (current === epoch) {
        loading = false;
        next.disabled = false;
      }
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving || !form.reportValidity()) return;
    const id = manual.value.trim() || model.value;
    if (!id) {
      showError({ message: "请选择模型或填写准确的模型 ID。", field: "model" });
      return;
    }
    const current = epoch;
    saving = true;
    save.disabled = next.disabled = cancel.disabled = true;
    error.hidden = true;
    try {
      await api.save({
        ...connection(),
        configuration_id: identity,
        expected_revision: profile?.revision || 0,
        model: id,
        effort: effort.value || "default",
        budget_tokens: Number(budget.value),
      });
      key.value = "";
      if (current !== epoch) return;
      baseline = shape();
      close(true);
      await onSaved();
    } catch (problem) {
      if (current === epoch) showError(problem);
    } finally {
      saving = false;
      save.disabled = next.disabled = cancel.disabled = false;
    }
  });
  return {
    dialog,
    get dirty() {
      return dirty();
    },
    close,
    open(catalog, item, button) {
      data = catalog;
      profile = item;
      identity =
        item?.id || "model_" + window.crypto.randomUUID().replaceAll("-", "");
      trigger = button;
      epoch++;
      form.reset();
      provider.replaceChildren();
      for (const p of [
        ...data.providers,
        { id: "custom", name: "自定义服务商" },
        { id: "local", name: "本地服务" },
      ])
        provider.append(optionText(p.name, p.id));
      credential.replaceChildren(optionText("新增 API Key", ""));
      for (const k of data.credentials.filter((k) => k.active))
        credential.append(new window.Option(k.name, k.id));
      credential.value = item?.credential_id || "";
      chooseKey();
      error.hidden = true;
      discovered = [];
      manual.closest("label").hidden = !item;
      model.replaceChildren(optionText("请选择模型", ""));
      selected.hidden = save.hidden = !item;
      if (item) {
        model.append(new window.Option(item.model, item.model));
        model.value = item.model;
        options();
        effort.value = item.effort;
      }
      budget.value = String(data.default_budget_tokens);
      setText(title, item ? uiText("编辑模型") : uiText("添加模型"));
      setText(status, uiText("读取模型只查询元数据；保存后可单独测试调用。"));
      baseline = shape();
      button?.setAttribute("aria-expanded", "true");
      dialog.showModal();
      (item ? model : provider).focus();
    },
  };
}
