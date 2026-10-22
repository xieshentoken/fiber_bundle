// Models & budget: the query routing configuration. It references credential files, never
// key text; saving never starts a call (every run is approved per task on Ask & search).
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  empty,
  field,
  input,
  skeleton,
  failure,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { usd } from "./format.js";

const t = strings({
  zh: {
    status: "配置状态",
    configured: "配置已保存；自动执行关闭",
    missing: "尚未配置模型",
    import: "导入路由配置文件",
    importHelp:
      "选择已准备、关闭自动执行的 query-routing-preparation/v1 JSON（≤ 64 KiB）。",
    importBad: "配置文件无法载入，请检查文件格式和大小。",
    importWrong: "请选择关闭自动执行的路由配置文件。",
    tooBig: "配置文件超过 64 KiB。",
    loaded: "配置已载入表单，请核对后保存。",
    none: "请导入已准备的路由配置文件。",
    local_query: "本地查询",
    cloud_answer: "云端答案整理",
    backup: "声明备用模型 {n}",
    unset: "尚未配置",
    save: "保存模型配置",
    discard: "放弃编辑并重新读取",
    discardAsk: "放弃未保存的模型配置编辑并重新读取？",
    saved: "模型配置已保存；仅逐任务明确启动。",
    savedKept: "提交的配置已保存；等待期间的新编辑保留，仍需明确保存。",
    kept: "保留未保存编辑；保存后才成为任务配置。",
    conflict:
      "服务器配置已变化；未保存编辑保留，原版本保存将拒绝冲突。可放弃编辑后刷新。",
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
    budget: "预算",
    perTask: "每任务 {v}",
    perDay: "每日 {v}",
    noKeys: "此处只保存凭证文件路径，不保存 API key 文本。",
    ask: "去提问",
  },
  en: {
    status: "Configuration",
    configured: "Saved; automatic runs off",
    missing: "No model configured",
    import: "Import routing file",
    importHelp:
      "Pick a prepared query-routing-preparation/v1 JSON with automatic runs disabled (≤ 64 KiB).",
    importBad: "The file couldn't be loaded. Check its format and size.",
    importWrong: "Choose a routing file with automatic execution disabled.",
    tooBig: "The file is larger than 64 KiB.",
    loaded: "Configuration loaded into the form. Review it, then save.",
    none: "Import a prepared routing configuration file.",
    local_query: "Local query",
    cloud_answer: "Cloud answer editing",
    backup: "Declared backup model {n}",
    unset: "Not configured",
    save: "Save model configuration",
    discard: "Discard edits and reload",
    discardAsk: "Discard unsaved model configuration edits and reload?",
    saved: "Model configuration saved. Runs only start when approved per task.",
    savedKept:
      "The submitted configuration was saved; newer edits are kept and still need saving.",
    kept: "Unsaved edits kept; they only apply after saving.",
    conflict:
      "The server configuration changed. Your edits are kept, but saving the old version will be rejected. Discard edits to reload.",
    image: "Restricted runtime image digest",
    endpoint: "Endpoint",
    model: "Model ID",
    secret_file: "Local credential file (absolute path)",
    max_input_tokens: "Max input tokens",
    max_output_tokens: "Max output tokens",
    max_request_bytes: "Max request bytes",
    max_calls: "Max calls per task",
    daily_calls: "Max calls per day",
    timeout_seconds: "Timeout per call (s)",
    price_revision: "Price revision",
    input_nusd: "Cost per input token (nano USD)",
    output_nusd: "Cost per output token (nano USD)",
    task_limit_nusd: "Cost limit per task (nano USD)",
    daily_limit_nusd: "Cost limit per day (nano USD)",
    max_attempts: "Max attempts",
    timezone: "Billing time zone",
    currency: "Currency",
    budget: "Budget",
    perTask: "{v} per task",
    perDay: "{v} per day",
    noKeys: "Only credential file paths are stored here — never API key text.",
    ask: "Go to Ask",
  },
});
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

export function createView(ctx) {
  let revision = null,
    value = null,
    baseline = null,
    epoch = 0,
    editRevision = 0,
    configured = false;
  const statusHost = h("div.a-toolbar");
  const fieldsHost = h("div.a-stack");
  const statusLine = h("p.a-subtle", { role: "status" });
  const dirty = () =>
    JSON.stringify(canonical(value)) !== JSON.stringify(canonical(baseline));
  const fileInput = h("input.a-sr", {
    type: "file",
    accept: ".json,application/json",
    id: "atlas-routing-file",
  });
  fileInput.addEventListener("change", async () => {
    try {
      const file = fileInput.files[0];
      if (!file) return;
      if (file.size > 65536) throw new Error(t("tooBig"));
      const mine = epoch;
      const data = JSON.parse(await file.text());
      if (mine !== epoch) return;
      if (
        data.schema !== "query-routing-preparation/v1" ||
        data.enabled !== false ||
        !Array.isArray(data.approved_backups)
      ) {
        toast(t("importWrong"), { tone: "error" });
        return;
      }
      value = data;
      editRevision++;
      draw();
      toast(t("loaded"), { tone: "success" });
    } catch {
      toast(t("importBad"), { tone: "error" });
    } finally {
      fileInput.value = "";
    }
  });
  function draw() {
    clear(
      statusHost,
      badge(
        configured ? t("configured") : t("missing"),
        configured ? "success" : "idle",
      ),
    );
    if (!value) {
      clear(fieldsHost, empty(t("none"), { iconName: "models" }));
      return;
    }
    const roles = [
      [t("local_query"), value.local_query],
      [t("cloud_answer"), value.cloud_answer],
      ...value.approved_backups.map((role, i) => [
        t("backup", { n: i + 1 }),
        role,
      ]),
    ];
    clear(
      fieldsHost,
      roles.map(([title, role]) => {
        if (!role)
          return h(
            "fieldset.a-fieldset",
            h("legend", title),
            h("p.a-subtle", t("unset")),
          );
        const budget = Number.isSafeInteger(role.task_limit_nusd)
          ? badge(
              `${t("perTask", { v: usd(role.task_limit_nusd) })}${Number.isSafeInteger(role.daily_limit_nusd) ? " · " + t("perDay", { v: usd(role.daily_limit_nusd) }) : ""}`,
              "info",
            )
          : null;
        return h(
          "fieldset.a-fieldset",
          h("legend", title),
          budget ? h("div.a-toolbar", icon("coin"), budget) : null,
          h(
            "div.a-form-grid",
            Object.entries(role).map(([key, initial]) => {
              const numeric = typeof initial === "number";
              const control = input({
                type: numeric ? "number" : "text",
                value: initial ?? "",
                required: key !== "secret_file",
                min: numeric ? (key.endsWith("nusd") ? 0 : 1) : undefined,
                step: numeric ? 1 : undefined,
              });
              control.addEventListener("input", () => {
                editRevision++;
                role[key] = numeric
                  ? Number(control.value)
                  : control.value || null;
              });
              const label = t(key);
              return field(label === key ? key : label, control, {
                help: key === "secret_file" ? t("noKeys") : undefined,
              });
            }),
          ),
        );
      }),
    );
  }
  const submit = h("button.a-button.is-primary", { type: "submit" }, t("save"));
  const form = h(
    "form.a-form",
    fieldsHost,
    h("div.a-form-actions", submit),
    statusLine,
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!value || !form.reportValidity()) return;
    void run(submit, async () => {
      const mine = epoch,
        submittedEdit = editRevision,
        submitted = structuredClone(value);
      const saved = await ctx.api.saveQueryPolicy({
        expected_sha256: revision,
        value: submitted,
      });
      if (mine !== epoch) return;
      revision = saved.configuration_sha256;
      baseline = structuredClone(saved.value);
      configured = true;
      if (submittedEdit === editRevision || !dirty()) {
        value = structuredClone(saved.value);
        draw();
        statusLine.textContent = t("saved");
        toast(t("saved"), { tone: "success" });
      } else {
        statusLine.textContent = t("savedKept");
        toast(t("savedKept"), { tone: "warning" });
      }
    });
  });
  async function refresh(discard = false) {
    const mine = ++epoch;
    if (!value) clear(fieldsHost, skeleton(5));
    let result;
    try {
      result = await ctx.api.queryPolicy();
    } catch (error) {
      clear(
        fieldsHost,
        failure(error, () => refresh(discard)),
      );
      return;
    }
    if (mine !== epoch) return;
    if (value && dirty() && !discard) {
      statusLine.textContent =
        result.configuration_sha256 === revision ? t("kept") : t("conflict");
      return;
    }
    revision = result.configuration_sha256;
    configured = !!result.configured;
    value = structuredClone(result.value);
    baseline = structuredClone(result.value);
    statusLine.textContent = "";
    draw();
  }
  const discardButton = button(t("discard"), {
    variant: "ghost",
    iconName: "refresh",
  });
  discardButton.addEventListener("click", () =>
    run(discardButton, async () => {
      if (dirty() && !window.confirm(t("discardAsk"))) return false;
      await refresh(true);
    }),
  );
  return {
    node: h(
      "div.a-stack",
      card(
        {
          title: t("status"),
          actions: h(
            "div.a-toolbar",
            fileInput,
            h(
              "label.a-button",
              { for: fileInput.id },
              icon("upload"),
              t("import"),
            ),
            discardButton,
          ),
        },
        statusHost,
        h("p.a-subtle", t("importHelp")),
      ),
      card({}, form),
    ),
    header: {
      actions: [
        button(t("ask"), {
          variant: "ghost",
          iconName: "ask",
          onClick: () => ctx.navigate("ask", "model"),
        }),
      ],
    },
    refresh: () => refresh(false),
    dirty: () => Boolean(value && dirty()),
    dispose: () => epoch++,
  };
}
