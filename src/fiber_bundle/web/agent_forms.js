import { joinText, setText, textNode, uiText, phrase } from "./i18n/index.js";
// Configuration inputs and bounded literal-term suggestions; no network or model calls.
import { el } from "./ui.js";
import { rememberDraft } from "./unsaved_changes.js";

function input(form, name, label, value, options = {}) {
  const wrapper = el("div"),
    field = el(options.select ? "select" : "input");
  field.id = "agent-" + name;
  field.name = name;
  const caption = el("label", uiText(label));
  caption.htmlFor = field.id;
  if (options.select) {
    for (const [id, title] of options.select) {
      const option = el("option", uiText(title));
      option.value = id;
      field.append(option);
    }
  } else {
    field.type = options.number ? "number" : "text";
    field.required = true;
    field.maxLength = options.maxLength || 100;
    if (options.number) {
      field.min = options.number[0];
      field.max = options.number[1];
    }
  }
  field.value = value;
  wrapper.append(caption, field);
  form.append(wrapper);
  return field;
}
export function connectionForm(vaults, catalog, configuration) {
  const form = el("form", null, "agent-form");
  form.id = "agent-config-form";
  form.dataset.draft = "tracked";
  const value = configuration || {
    name: "",
    agent_type: "hermes",
    vault_ids: [],
    answer_policy: "augmented",
    model_configuration_id:
      catalog.models.find((m) => m.id === "app-default")?.id ||
      catalog.models.find((m) => m.processing_location === "local")?.id ||
      "",
    limits: {
      queries: 2,
      results_per_query: 5,
      evidence_chars: 8000,
      prompt_bytes: 32768,
      timeout_seconds: 60,
      output_tokens: 1024,
    },
  };
  input(form, "name", "1. 连接名称", value.name);
  const type = input(form, "type", "Agent 类型", value.agent_type, {
    select: [
      ["hermes", "本机 Hermes"],
      ["mcp", "通用 MCP 客户端"],
    ],
  });
  const installed = catalog.hermes;
  form.append(
    el(
      "p",
      installed.state === "candidate"
        ? phrase("已识别 Hermes {p0}，实际接入仍需检查。", {
            p0: installed.version,
          })
        : uiText("Hermes 未找到或版本不兼容；可创建通用 MCP 连接。"),
      "muted",
    ),
  );
  const scope = el("fieldset"),
    legend = el("legend", uiText("2. 明确授权知识库（仅查询）"));
  scope.append(legend);
  for (const vault of vaults.filter(
    (v) => v.status === "active" && v.attachment_state !== "detached",
  )) {
    const label = el("label", null, "check"),
      checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.name = "vault_ids";
    checkbox.value = vault.id;
    checkbox.checked = value.vault_ids.includes(vault.id);
    label.append(checkbox, document.createTextNode(vault.name));
    scope.append(label);
  }
  if (!scope.querySelector("input"))
    scope.append(el("p", uiText("暂无可授权知识库。")));
  form.append(scope);
  input(form, "quality", "资料质量范围", value.quality_scope || "all", {
    select: [
      ["all", "全部可见资料（显示校验状态）"],
      ["complete", "仅完整性通过（不等于事实审核）"],
    ],
  });
  const originalsLabel = el("label"),
    originals = el("input");
  originals.type = "checkbox";
  originals.name = "originals";
  originals.checked = value.allow_originals || false;
  originalsLabel.append(
    originals,
    textNode(uiText("可选：允许此连接读取已选资料的原件与附件")),
  );
  form.append(originalsLabel);
  input(form, "policy", "3. 回答规则", value.answer_policy, {
    select: [
      ["augmented", "知识库优先，可补充一般知识"],
      ["evidence_only", "仅依据知识库"],
    ],
  });
  const model = input(
    form,
    "model",
    "受管模型配置",
    value.model_configuration_id || "",
    {
      select: [
        ["", "请选择已配置模型"],
        ...catalog.models.map((m) => [
          m.id,
          joinText(
            [
              m.name,
              m.processing_location === "managed"
                ? uiText("提交时解析")
                : m.processing_location === "cloud"
                  ? uiText("云端")
                  : uiText("本机"),
            ],
            " · ",
          ),
        ]),
      ],
    },
  );
  const location = el("p", null, "muted");
  form.append(location);
  function layout() {
    model.disabled = type.value === "mcp";
    const selected = catalog.models.find((m) => m.id === model.value);
    setText(
      location,
      type.value === "mcp"
        ? uiText("通用 MCP 的模型、数据发送与费用由外部宿主控制。")
        : selected?.processing_location === "managed"
          ? uiText(
              "新任务解析当时的应用默认模型并冻结配置；云端模型会接收问题及获授权的检索片段，使用该 Key 的共享预算。",
            )
          : selected?.processing_location === "cloud"
            ? phrase(
                "显式问答测试会将问题及所选知识库的检索片段发送到 {p0} 云端（{p1}），使用该 Key 的共享预算与任务限额。保存、应用和连接检查不会发送资料或调用模型。备用不启动。",
                {
                  p0: selected.provider || "所选服务商",
                  p1: selected.endpoint,
                },
              )
            : uiText(
                "Hermes 将问题与所选库的检索片段发送到选定本机模型。备用不启动。",
              ),
    );
  }
  type.addEventListener("change", layout);
  model.addEventListener("change", layout);
  layout();
  const limits = el("details"),
    summary = el("summary", uiText("检索与模型限额"));
  limits.append(summary);
  for (const [name, label, range] of [
    ["queries", "每题最多检索次数", [1, 2]],
    ["results_per_query", "每次最多命中", [1, 5]],
    ["evidence_chars", "总证据字符", [400, 16000]],
    ["prompt_bytes", "完整提示字节", [4096, 65536]],
    ["timeout_seconds", "硬截止（秒）", [1, 180]],
    ["output_tokens", "最多输出 tokens", [128, 4096]],
  ])
    input(limits, name, label, value.limits[name], { number: range });
  limits.append(
    el(
      "p",
      uiText(
        "每题最多一次生成，未知用量不重试；每连接每天最多 10 次提交，且受模型配置额度约束。",
      ),
    ),
  );
  form.append(limits);
  const save = el(
    "button",
    configuration ? uiText("保存并暂停") : uiText("保存暂停连接"),
    "primary",
  );
  save.type = "submit";
  form.append(save);
  rememberDraft(form);
  return form;
}
export function readConfiguration(form) {
  const get = (name) => form.elements.namedItem(name).value;
  const vault_ids = Array.from(
    form.querySelectorAll('[name="vault_ids"]:checked'),
  ).map((n) => n.value);
  if (!vault_ids.length) throw new Error("请明确选择至少一个知识库。");
  if (get("type") === "hermes" && !get("model"))
    throw new Error("请先在模型页配置模型，再明确选择此配置。");
  return {
    schema_version: "fiber-agent-connection/v2",
    quality_scope: get("quality"),
    allow_originals: form.elements.namedItem("originals")?.checked || false,
    name: get("name"),
    agent_type: get("type"),
    vault_ids,
    answer_policy: get("policy"),
    model_configuration_id: get("type") === "hermes" ? get("model") : null,
    limits: Object.fromEntries(
      [
        "queries",
        "results_per_query",
        "evidence_chars",
        "prompt_bytes",
        "timeout_seconds",
        "output_tokens",
      ]
        .map((key) => [key, Number(get(key))])
        .concat([["provider_calls", 1]]),
    ),
  };
}
export function suggestTerms(question) {
  const text = question.normalize("NFKC").trim();
  const quoted = Array.from(text.matchAll(/[“「"]([^”」"]{2,40})[”」"]/g)).map(
    (m) => m[1],
  );
  const models =
    text.match(/[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*/g) || [];
  const subject = text.match(
    /^(?:请(?:问|说明|解释)?|关于)?([\p{Script=Han}]{2,20}?)(?:的|与|和)/u,
  )?.[1];
  const result = [
    ...new Set([...quoted, ...models, ...(subject ? [subject] : [])]),
  ].slice(0, 2);
  if (!result.length && text.length <= 16 && !/[，,。！？?]/.test(text))
    result.push(text);
  return result;
}
