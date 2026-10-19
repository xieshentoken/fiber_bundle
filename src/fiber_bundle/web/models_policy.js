import { setText, translate, uiText } from "./i18n/index.js";
// Routing configuration contains references to credential files, never key text.
import { $, action, el, captureMessage, setBusy } from "./ui.js";

const labels = {
  image: "受限执行镜像摘要",
  endpoint: "服务地址",
  model: "模型 ID",
  secret_file: "本机凭证文件绝对路径",
  max_input_tokens: "输入 token 上限",
  max_output_tokens: "输出 token 上限",
  max_request_bytes: "请求字节上限",
  max_calls: "每任务调用上限",
  daily_calls: "每日调用上限",
  timeout_seconds: "单次超时秒数",
  price_revision: "价格版本",
  input_nusd: "每输入 token 费用（nano USD）",
  output_nusd: "每输出 token 费用（nano USD）",
  task_limit_nusd: "每任务费用上限（nano USD）",
  daily_limit_nusd: "每日费用上限（nano USD）",
  max_attempts: "最大尝试次数",
  timezone: "账单时区",
  currency: "货币",
};
export function createPolicyEditor(api) {
  let revision = null,
    value = null,
    epoch = 0,
    baseline = null,
    editRevision = 0;
  function canonical(data) {
    if (Array.isArray(data)) return data.map(canonical);
    if (data && typeof data === "object")
      return Object.fromEntries(
        Object.keys(data)
          .sort()
          .map((key) => [key, canonical(data[key])]),
      );
    return data;
  }
  function dirty() {
    return (
      JSON.stringify(canonical(value)) !== JSON.stringify(canonical(baseline))
    );
  }
  function render() {
    const container = $("query-policy-fields");
    container.replaceChildren();
    if (!value) {
      container.append(el("p", uiText("请导入已准备的路由配置文件。")));
      return;
    }
    const roles = [
      ["本地查询", value.local_query],
      ["云端答案整理", value.cloud_answer],
      ...value.approved_backups.map((v, i) => ["声明备用模型 " + (i + 1), v]),
    ];
    for (const [title, role] of roles) {
      const group = el("fieldset");
      group.append(el("legend", uiText(title)));
      if (!role) {
        group.append(el("p", uiText("尚未配置")));
        container.append(group);
        continue;
      }
      for (const [key, initial] of Object.entries(role)) {
        const label = el("label", uiText(labels[key] || key)),
          input = el("input");
        input.type = typeof initial === "number" ? "number" : "text";
        input.value = initial ?? "";
        input.required = key !== "secret_file";
        if (input.type === "number") {
          input.min = key.endsWith("nusd") ? "0" : "1";
          input.step = "1";
        }
        input.addEventListener("input", () => {
          editRevision++;
          role[key] =
            input.type === "number" ? Number(input.value) : input.value || null;
        });
        label.append(input);
        group.append(label);
      }
      container.append(group);
    }
  }
  $("query-policy-import").addEventListener("change", async (event) => {
    const notify = captureMessage("models");
    try {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > 65536) throw new Error("配置文件超过 64 KiB。");
      const current = epoch;
      const data = JSON.parse(await file.text());
      if (current !== epoch) return;
      if (
        data.schema !== "query-routing-preparation/v1" ||
        data.enabled !== false ||
        !Array.isArray(data.approved_backups)
      )
        throw new Error("请选择关闭自动执行的路由配置文件。");
      value = data;
      editRevision++;
      render();
      notify("配置已载入表单，请核对后保存。");
    } catch {
      notify("配置文件无法载入，请检查文件格式和大小。", true);
    } finally {
      event.target.value = "";
    }
  });
  $("query-policy-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!value || !event.target.reportValidity()) return;
    const current = epoch,
      submittedEdit = editRevision,
      submitted = globalThis.structuredClone(value),
      notify = captureMessage("models"),
      button = event.submitter;
    button.disabled = true;
    setBusy(button, true);
    try {
      const saved = await api.saveQueryPolicy({
        expected_sha256: revision,
        value: submitted,
      });
      if (current !== epoch) return;
      revision = saved.configuration_sha256;
      baseline = globalThis.structuredClone(saved.value);
      if (submittedEdit === editRevision || !dirty()) {
        value = globalThis.structuredClone(saved.value);
        render();
        setText(
          $("query-policy-status"),
          uiText("配置已保存；仅逐任务明确启动。"),
        );
        notify("模型配置已保存。");
      } else {
        setText(
          $("query-policy-status"),
          uiText("提交的配置已保存；保留等待期间的新编辑，仍需明确保存。"),
        );
        notify("提交的配置已保存；表单还有未保存编辑。");
      }
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(button, false);
      button.disabled = false;
    }
  });
  async function refresh(discard = false) {
    const current = ++epoch,
      result = await api.queryPolicy();
    if (current !== epoch) return;
    if (dirty() && !discard) {
      setText(
        $("query-policy-status"),
        result.configuration_sha256 === revision
          ? uiText("保留未保存编辑；保存后才成为任务配置。")
          : uiText(
              "服务器配置已变化；未保存编辑保留，原版本保存将拒绝冲突。可明确放弃编辑后刷新。",
            ),
      );
      return;
    }
    revision = result.configuration_sha256;
    value = globalThis.structuredClone(result.value);
    baseline = globalThis.structuredClone(result.value);
    render();
    setText(
      $("query-policy-status"),
      result.configured
        ? uiText("配置已保存；自动执行关闭。")
        : uiText("尚未配置模型。"),
    );
  }
  action($("query-policy-discard"), async () => {
    if (
      dirty() &&
      !window.confirm(translate("放弃未保存的模型配置编辑并重新读取？"))
    )
      return;
    await refresh(true);
  });
  return {
    clear() {
      epoch++;
      revision = null;
      value = null;
      baseline = null;
      render();
      setText($("query-policy-status"), uiText(""));
    },
    refresh,
    get hasUnsavedChanges() {
      return dirty();
    },
    get digest() {
      return revision;
    },
  };
}
