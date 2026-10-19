import { setText, uiText } from "./i18n/index.js";
// One administrator processing configuration; new tasks freeze its saved revision.
import { $, el, captureMessage } from "./ui.js";
import { createProjectSkillsView } from "./project_skills.js";
import {
  rememberDraft,
  captureDraftSave,
  hasUnsavedChanges,
} from "./unsaved_changes.js";
const labels = {
  source_bytes: "单文件字节上限",
  pdf_pages: "PDF 页数上限",
  markdown_bytes: "单 Markdown 字节上限",
  markdown_total_bytes: "Markdown 总字节上限",
  text_bytes: "提取文本字节上限",
  result_bytes: "候选结果字节上限",
  image_pixels: "图片总像素上限",
  image_dimension: "图片边长上限",
  batch_bytes: "单批字节上限",
};
export function createSourcePolicyView(api, changed) {
  let policy = null,
    epoch = 0;
  const root = () => $("source-policy");
  const skills = createProjectSkillsView(api, root);
  function clear() {
    epoch++;
    root().replaceChildren();
    policy = null;
    skills.clear();
  }
  async function refresh() {
    const selected = ++epoch,
      next = await api.sourcePolicy();
    if (selected !== epoch) return;
    let form = $("source-policy-form");
    if (form && hasUnsavedChanges(form)) {
      await skills.refresh();
      return;
    }
    policy = next;
    if (!form) {
      const details = el("details"),
        status = el("p");
      details.id = "source-policy-settings";
      details.append(el("summary", uiText("资料处理设置")));
      form = el("form");
      form.id = "source-policy-form";
      form.dataset.draft = "tracked";
      for (const [key, title] of [
        ["document_image", "固定文档 Worker 镜像摘要"],
        ["article_image", "固定文章 Worker 镜像摘要（可留空）"],
        ["timeout_seconds", "任务超时秒数"],
      ]) {
        const label = el("label", uiText(title)),
          field = el("input");
        field.id = "source-policy-" + key;
        field.type = key === "timeout_seconds" ? "number" : "text";
        if (field.type === "number") {
          field.min = "1";
          field.max = "300";
          field.required = true;
        }
        label.append(field);
        form.append(label);
      }
      for (const [key, title] of Object.entries(labels)) {
        const label = el("label", uiText(title)),
          field = el("input");
        field.id = "source-policy-limit-" + key;
        field.type = "number";
        field.min = "1";
        field.required = true;
        label.append(field);
        form.append(label);
      }
      for (const [key, title] of [
        ["ocr_runtime", "OCR 资格凭据 JSON（可留空）"],
        ["conversion_runtime", "DOC 转换资格凭据 JSON（可留空）"],
        ["expert_runtime", "完整 fake-expert 固定版本资格凭据 JSON（可留空）"],
      ]) {
        const label = el("label", uiText(title)),
          field = el("textarea");
        field.id = "source-policy-" + key;
        field.maxLength = 16384;
        label.append(field);
        form.append(label);
      }
      const modeLabel = el("label", uiText("单 Markdown 超限处理")),
        mode = el("select");
      mode.id = "source-policy-oversize_action";
      for (const [value, text] of [
        ["pause", "暂停（保留旧任务语义）"],
        ["split", "按完整结构分片，无法安全拆分则暂停"],
      ]) {
        const option = el("option", uiText(text));
        option.value = value;
        mode.append(option);
      }
      modeLabel.append(mode);
      form.append(modeLabel);
      const save = el("button", uiText("保存资料处理设置"));
      save.type = "submit";
      form.append(
        el(
          "p",
          uiText(
            "总量限额仍生效。分片会保留正文并生成目录；不可拆分结构和跨段引用会暂停。设置只影响新任务。",
          ),
        ),
        save,
      );
      details.append(form, status);
      root().append(details);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        const submittedEpoch = epoch,
          acknowledge = captureDraftSave(form),
          notify = captureMessage("inventory", "source-policy");
        const value = {
          ...policy.effective_policy,
          revision:
            policy.value === null ? 1 : policy.effective_policy.revision + 1,
          document_image:
            $("source-policy-document_image").value.trim() || null,
          article_image: $("source-policy-article_image").value.trim() || null,
          timeout_seconds: Number($("source-policy-timeout_seconds").value),
          oversize_action: $("source-policy-oversize_action").value,
          limits: Object.fromEntries(
            Object.keys(labels).map((key) => [
              key,
              Number($("source-policy-limit-" + key).value),
            ]),
          ),
        };
        try {
          const receipts = Object.fromEntries(
            ["ocr_runtime", "conversion_runtime", "expert_runtime"].map(
              (key) => [
                key,
                $("source-policy-" + key).value.trim()
                  ? JSON.parse($("source-policy-" + key).value)
                  : null,
              ],
            ),
          );
          if (
            policy.effective_policy.schema >= 3 ||
            Object.values(receipts).some(Boolean)
          )
            Object.assign(value, receipts, {
              schema:
                receipts.expert_runtime || policy.effective_policy.schema === 4
                  ? 4
                  : 3,
            });
          if (value.schema === 3) delete value.expert_runtime;
        } catch {
          notify("运行资格凭据需要有效 JSON。", true);
          return;
        }
        save.disabled = true;
        try {
          const result = await api.saveSourcePolicy({
            value,
            expected_sha256: policy.configuration_sha256,
          });
          if (submittedEpoch !== epoch) return;
          policy = result;
          acknowledge();
          setText(status, uiText("资料处理设置已保存，只影响新任务。"));
          notify(status.textContent);
          await changed();
        } catch (error) {
          if (submittedEpoch === epoch) notify(error.message, true);
        } finally {
          save.disabled = false;
        }
      });
    }
    $("source-policy-oversize_action").value =
      policy.effective_policy.oversize_action;
    for (const key of ["document_image", "article_image", "timeout_seconds"])
      $("source-policy-" + key).value = policy.effective_policy[key] ?? "";
    for (const key of Object.keys(labels)) {
      const field = $("source-policy-limit-" + key);
      field.value = policy.effective_policy.limits[key];
      field.max = policy.capacity[key];
    }
    for (const key of ["ocr_runtime", "conversion_runtime", "expert_runtime"])
      $("source-policy-" + key).value = policy.effective_policy[key]
        ? JSON.stringify(policy.effective_policy[key])
        : "";
    rememberDraft(form);
    await skills.refresh();
  }
  return { clear, refresh };
}
