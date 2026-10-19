import { uiText } from "./i18n/index.js";
// Question editing and bounded local retrieval-term preparation; no direct model calls.
import { el, setBusy } from "./ui.js";
import { control } from "./record_ui.js";
import { captureDraftSave } from "./unsaved_changes.js";
import { suggestTerms } from "./agent_forms.js";

export function describeTest(item, vaults, catalog) {
  const model = catalog.models.find(
    (m) => m.id === item.configuration.model_configuration_id,
  );
  const location =
    model?.processing_location === "managed"
      ? "提交时冻结应用默认模型；若为云端，将发送问题与这些库的检索片段，使用该 Key 的共享预算"
      : model?.processing_location === "cloud"
        ? `${model.provider || (model.id === "opencodego-primary" ? "OpenCodeGo" : "所选服务商")} 云端；本次会发送问题与这些库的检索片段`
        : "本机处理";
  return `范围：${item.configuration.vault_ids.map((id) => vaults.find((v) => v.id === id)?.name || id).join("、")} · 模型 ${model?.name || "待配置"} · ${model?.endpoint || "待配置"} · ${location} · 一次生成 · 最多 ${item.configuration.limits.timeout_seconds} 秒。测试不会保存为知识。`;
}
export function testForm(
  item,
  { vaults, catalog, getCurrent, start, notify, isRunning = () => false },
) {
  const test = el("form", null, "agent-test");
  test.dataset.draft = "tracked";
  const caption = el("label", uiText("测试问题"));
  caption.htmlFor = "agent-question";
  const question = el("textarea");
  question.id = "agent-question";
  question.required = true;
  question.maxLength = 2000;
  question.rows = 3;
  const keywordLabel = el(
    "label",
    uiText("本轮检索词（每行一次查询，最多两行；空格连接的词须同时命中）"),
  );
  keywordLabel.htmlFor = "agent-test-queries";
  const keywords = el("textarea");
  keywords.id = "agent-test-queries";
  keywords.required = true;
  keywords.rows = 2;
  keywords.maxLength = 401;
  const prepare = control("从问题建议检索词（本机，无模型）", () => {
    const terms = suggestTerms(question.value);
    if (!terms.length)
      throw new Error("无法可靠选词，请填写明确术语、型号或缩短问题。");
    keywords.value = terms
      .slice(0, (getCurrent() || item).configuration.limits.queries)
      .join("\n");
    keywords.dispatchEvent(new window.Event("input", { bubbles: true }));
    notify("已填入候选检索词，请核对后再测试。");
  });
  const button = el("button", uiText("执行 Hermes 问答测试"), "primary");
  button.type = "submit";
  button.disabled = item.access_state !== "enabled";
  test.append(
    el("h3", uiText("受管问答测试")),
    el("p", describeTest(item, vaults, catalog), "muted agent-test-scope"),
    caption,
    question,
    prepare,
    keywordLabel,
    keywords,
    button,
  );
  test.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled || isRunning()) return;
    const acknowledge = captureDraftSave(test);
    button.disabled = true;
    setBusy(button, true);
    try {
      const queries = keywords.value
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      if (
        !queries.length ||
        queries.length > (getCurrent() || item).configuration.limits.queries ||
        queries.some((s) => s.length > 200 || s.split(/\s+/).length > 8)
      )
        throw new Error("请填写限额内的字面检索词。");
      await start("test_query", {
        request_id: window.crypto.randomUUID(),
        question: question.value,
        retrieval_queries: queries,
      });
      acknowledge();
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(button, false);
      button.disabled =
        (getCurrent() || item).access_state !== "enabled" || isRunning();
    }
  });
  return test;
}
