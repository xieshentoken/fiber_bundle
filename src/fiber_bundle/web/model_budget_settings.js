import {
  optionText,
  setText,
  translate,
  uiText,
  phrase,
} from "./i18n/index.js";
// Per-key budget controls do not read credentials or call a provider.
import { el, message } from "./ui.js";
import {
  rememberDraft,
  captureDraftSave,
  hasUnsavedChanges,
} from "./unsaved_changes.js";

export function modelBudgetSettings(api) {
  const root = el("details"),
    content = el("div");
  root.append(el("summary", uiText("统一模型预算")), content);
  let generation = 0;
  function clear() {
    generation++;
    content.replaceChildren();
    root.open = false;
  }
  async function load() {
    if (!root.open || content.querySelector("form")) return;
    const current = ++generation;
    try {
      const data = await api.modelBudgets();
      if (current !== generation || !root.isConnected) return;
      const keys = new Map(
        data.credentials.map((k) => [k.id, { id: k.id, label: k.label }]),
      );
      for (const row of data.budgets)
        keys.set(row.credential_id, {
          id: row.credential_id,
          label: row.settings.label,
        });
      if (!keys.size) {
        content.replaceChildren(
          el(
            "p",
            uiText("请先在模型与查询中配置 API Key；基础提取无需 Key 或预算。"),
          ),
        );
        return;
      }
      const form = el("form"),
        result = el("p");
      form.dataset.draft = "tracked";
      form.className = "uv-policy-form";
      function field(title, name, values) {
        const label = el("label", uiText(title)),
          input = el(values ? "select" : "input");
        input.name = name;
        if (values)
          for (const [value, text] of values)
            input.append(optionText(text, value));
        label.append(input);
        form.append(label);
        return input;
      }
      const key = field(
        "API Key",
        "credential_id",
        [...keys].map(([id, k]) => [id, k.label]),
      );
      const label = field("显示名称", "label"),
        unit = field("预算单位", "unit", [
          ["tokens", "Tokens"],
          ["USD_nano", "USD"],
        ]);
      label.required = true;
      label.maxLength = 100;
      const amount = field("周期上限（0 暂停付费步骤）", "limit");
      amount.type = "number";
      amount.min = "0";
      amount.required = true;
      const period = field("周期", "period", [
        ["day", "每日"],
        ["month", "每月"],
      ]);
      const status = el("p");
      const button = el("button", uiText("保存模型预算"));
      button.type = "submit";
      form.append(
        status,
        el(
          "p",
          uiText(
            "预算由使用此 Key 的 Fiber 模型任务共享；基础提取无额度要求。外部 Agent 自己调用模型的消费由其宿主管理，服务商余额未知。",
          ),
          "muted",
        ),
        button,
        result,
      );
      let selected = null;
      function choose() {
        selected =
          data.budgets.find((b) => b.credential_id === key.value) || null;
        label.value = selected?.settings.label || keys.get(key.value).label;
        unit.value = selected?.settings.unit || "tokens";
        period.value = selected?.settings.period || "day";
        unit.disabled = period.disabled = Boolean(selected);
        amount.step = unit.value === "tokens" ? "1" : "0.01";
        amount.value = String(
          selected
            ? selected.settings.limit / (unit.value === "tokens" ? 1 : 1e9)
            : 100000,
        );
        setText(
          status,
          selected
            ? phrase("本周期已用 {p0} {p1}（含未核对预留）", {
                p0: selected.used / (unit.value === "tokens" ? 1 : 1e9),
                p1: unit.value === "tokens" ? "tokens" : "USD",
              })
            : uiText("尚未设置统一预算，当前仍使用已有模型配置限额。"),
        );
        rememberDraft(form);
      }
      key.addEventListener("change", () => {
        if (
          hasUnsavedChanges(form) &&
          !window.confirm(
            translate(
              "当前预算有未保存的更改，切换 Key 将放弃这些更改。确定切换？",
            ),
          )
        ) {
          key.value = selected?.credential_id || [...keys.keys()][0];
          return;
        }
        choose();
      });
      unit.addEventListener("change", () => {
        amount.step = unit.value === "tokens" ? "1" : "0.01";
      });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        const acknowledge = captureDraftSave(form),
          id = key.value;
        const body = {
          credential_id: id,
          expected_revision: selected?.revision || 0,
          settings: {
            label: label.value.trim(),
            unit: unit.value,
            limit: Math.round(
              Number(amount.value) * (unit.value === "tokens" ? 1 : 1e9),
            ),
            period: period.value,
            timezone: selected?.settings.timezone || "Asia/Taipei",
          },
        };
        button.disabled = true;
        try {
          const saved = await api.saveModelBudget(body);
          if (current !== generation || !root.isConnected || key.value !== id)
            return;
          data.budgets = data.budgets
            .filter((b) => b.credential_id !== id)
            .concat(saved);
          selected = saved;
          acknowledge();
          setText(result, uiText("模型预算已保存；历史消费和未知预留保留。"));
          message(result.textContent, false, "execution");
          unit.disabled = period.disabled = true;
        } catch (error) {
          if (current === generation) setText(result, uiText(error.message));
        } finally {
          button.disabled = false;
        }
      });
      content.replaceChildren(form);
      choose();
    } catch (error) {
      if (current === generation) setText(content, uiText(error.message));
    }
  }
  root.addEventListener("toggle", () => {
    load();
  });
  return { root, clear };
}
