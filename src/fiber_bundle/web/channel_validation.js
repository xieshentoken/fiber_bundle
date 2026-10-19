import { setText, uiText } from "./i18n/index.js";
// Field names and finite rule codes only; no credentials or backend exception text.
const definitions = {
  add_read_only: [
    "vault",
    "所选知识库为只读，不能勾选‘允许入库’；请选择可写库，或取消此权限及依赖它的专家指令。",
  ],
  client_unavailable: [
    "client",
    "‘本人访问身份’已停用或不存在，请在‘Agent 与客户端’核对身份，再重新选择。",
  ],
  name_invalid: ["name", "‘名称’不能为空且不能超过100个字符，请修改名称。"],
  app_id_invalid: [
    "app-id",
    "‘App ID’格式无效，请填写以cli_开头的飞书应用ID。",
  ],
  client_id_invalid: ["client", "请选择有效的‘本人访问身份’。"],
  vault_id_invalid: ["vault", "请选择有效的‘默认知识库’。"],
  runtime_manifest_invalid: [
    "manifest",
    "请填写有效的‘Hermes固定运行清单绝对路径’。",
  ],
  query_limit_invalid: ["query-limit", "‘最多接收查询’必须为0到100的整数。"],
  outbound_limit_invalid: [
    "outbound-limit",
    "‘最多手机回复’必须为0到100的整数。",
  ],
  model_limit_invalid: ["model-limit", "‘最多模型调用’必须为0到100的整数。"],
  download_limit_invalid: [
    "download-limit",
    "‘最多飞书附件下载’必须为0到100的整数。",
  ],
  expert_limit_invalid: [
    "expert-limit",
    "‘本次配置的专家指令上限’必须为0到100的整数。",
  ],
  expert_receipt_limit_invalid: [
    "expert-receipt-limit",
    "‘专家状态回执上限’必须为0到100的整数。",
  ],
  app_secret_invalid: ["secret", "‘App Secret’输入格式无效，请核对凭据字段。"],
  credential_file_invalid: [
    "credential",
    "‘本机凭证文件绝对路径’格式无效，请重新选择文件。",
  ],

  expert_feishu_required: [
    "platform",
    "专家文件指令只支持飞书，请检查‘平台’。",
  ],
  expert_original_required: [
    "original",
    "专家文件指令需要勾选‘允许取回原件’。",
  ],
  expert_add_required: ["add", "专家文件指令需要勾选‘允许入库’。"],
  expert_limit_required: [
    "expert-limit",
    "已开启专家文件指令，请将‘本次配置的专家指令上限’设为正数。",
  ],
  expert_limits_disabled: [
    "expert-limit",
    "专家文件指令已关闭，请将‘专家指令上限’和‘专家状态回执上限’都设为0。",
  ],
  query_limit_required: [
    "query-limit",
    "‘最多接收查询’为0；请编辑入口配置，设为正数并保存后再启动。",
  ],
  outbound_limit_required: [
    "outbound-limit",
    "‘最多手机回复’为0；请编辑入口配置，设为正数并保存后再启动。",
  ],
  model_limit_required: [
    "model-limit",
    "当前选择了模型回答；请将‘最多模型调用’设为正数，或改选‘原文证据，零模型调用’，保存后再启动。",
  ],
  query_permission_required: [
    "client",
    "所选身份没有目标知识库的查询权限，或身份/知识库已停用；请在‘Agent 与客户端’核对查询授权和知识库接入状态。",
  ],
  original_permission_required: [
    "original",
    "此入口要求原件权限，但当前授权不可用；请在‘Agent 与客户端’核对目标库的原件授权。",
  ],
  add_permission_required: [
    "add",
    "此入口要求入库权限，但当前授权不可用；请在‘Agent 与客户端’核对目标库的新增授权，并确认知识库可写。",
  ],
  vault_unavailable: [
    "vault",
    "目标知识库目录或范围不可访问，请在‘知识库’核对接入目录和包含/排除范围。",
  ],
};
export function channelIssueText(code) {
  return definitions[code]?.[1];
}
export function channelIssuesText(issues) {
  return Array.isArray(issues)
    ? [...new Set(issues)].map(channelIssueText).filter(Boolean).join(" ")
    : "";
}
export function configurationIssues(value) {
  const issues = [];
  if (value.expert_enabled) {
    if (value.platform !== "feishu") issues.push("expert_feishu_required");
    if (!value.original) issues.push("expert_original_required");
    if (!value.add) issues.push("expert_add_required");
    if (value.policy_version !== 2 && !(value.expert_limit > 0))
      issues.push("expert_limit_required");
  } else if (value.expert_limit || value.expert_receipt_limit)
    issues.push("expert_limits_disabled");
  return issues;
}
export function showChannelIssues(root, issues, field) {
  root.replaceChildren();
  root.hidden = !issues.length;
  root.setAttribute("role", "alert");
  for (const node of root.parentElement?.querySelectorAll(
    '[data-channel-invalid="true"]',
  ) || []) {
    node.removeAttribute("aria-invalid");
    node.removeAttribute("aria-describedby");
    delete node.dataset.channelInvalid;
  }
  for (const code of issues) {
    if (!definitions[code]) continue;
    const line = document.createElement("p");
    setText(line, uiText(definitions[code][1]));
    root.append(line);
    const node = field?.(definitions[code][0]);
    if (node) {
      node.setAttribute("aria-invalid", "true");
      node.setAttribute("aria-describedby", root.id);
      node.dataset.channelInvalid = "true";
    }
  }
}
