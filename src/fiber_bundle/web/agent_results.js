import { joinText, phrase, uiText } from "./i18n/index.js";
// Safe draft/source presentation. All strings become text nodes; never HTML/model instructions.
import { el } from "./ui.js";
import { showJSON } from "./record_ui.js";
import { agentError, operationState, operationTitle } from "./agent_labels.js";
import { expertResults } from "./expert_results.js";
export const accessLabels = {
  paused: "访问已暂停",
  enabled: "查询已授权",
  revoked: "已永久撤销",
  repair_required: "访问需修复",
};
export function states(parent, row) {
  const group = el("div", null, "agent-states");
  for (const title of [
    accessLabels[row.access_state],
    {
      applied: "配置已应用",
      pending: "配置待应用",
      outdated: "配置需重新应用",
      repair_required: "配置需修复",
      external_configuration_required: "MCP 配置已生成 · 待外部导入",
    }[row.configuration_state],
    row.probe.state === "passed"
      ? "MCP 工具可用"
      : phrase("连接检查：{p0}", { p0: uiText(check(row.probe.state)) }),
    row.test.state === "passed"
      ? "先检索后回答已验证"
      : phrase("问答测试：{p0}", { p0: uiText(check(row.test.state)) }),
  ])
    group.append(el("span", uiText(title), "status-badge info"));
  parent.append(group);
}
function check(value) {
  return (
    {
      not_run: "未运行",
      outdated: "已过期",
      failed: "失败",
      cancelled: "已取消",
      passed: "通过",
    }[value] || value
  );
}
export function operationResult(parent, operation, readExpertMedia) {
  parent.replaceChildren();
  parent.dataset.state = operation.state;
  parent.dataset.kind = operation.kind;
  parent.dataset.operationId = operation.id;
  parent.append(
    el(
      "p",
      joinText(
        [
          uiText(operationTitle(operation.kind)),
          uiText(operationState(operation.state)),
        ],
        " · ",
      ),
      "muted",
    ),
  );
  if (operation.error_code)
    parent.append(el("p", uiText(agentError(operation.error_code)), "error"));
  if (operation.result?.state === "outdated")
    parent.append(
      el(
        "p",
        uiText("结果已过期：当前权限、配置或来源已变化。请显式重新测试。"),
      ),
    );
  if (
    operation.result?.provider_error &&
    operation.result.provider_error !== operation.error_code
  )
    parent.append(el("p", uiText(agentError(operation.result.provider_error))));
  if (operation.result?.usage_state === "unknown")
    parent.append(
      el("p", uiText("本次模型用量未知，系统不会自动重试。"), "muted"),
    );
  if (operation.result?.indices) {
    parent.append(
      el("p", uiText("已通过实际 stdio 工具发现及范围检查，没有调用模型。")),
    );
    for (const index of operation.result.indices)
      parent.append(
        el(
          "p",
          uiText(
            {
              ready: "索引就绪",
              absent: "索引未建立：检索使用当前只读快照",
              stale: "索引已过期：请在知识库页显式更新后再问答",
            }[index.state] || index.state,
          ),
        ),
      );
  }
  if (!operation.answer) return;
  const answer = operation.answer;
  parent.append(el("h3", uiText("回答草稿 · 尚未进行语义核实")));
  parent.append(
    el(
      "p",
      phrase("检索词：{p0} · 来源 {p1} 条", {
        p0: answer.retrieval_queries.join("；"),
        p1: answer.sources.length,
      }),
    ),
  );
  if (!answer.claims.length)
    parent.append(el("p", uiText("知识库证据不足，未生成知识库事实。")));
  for (const claim of answer.claims)
    parent.append(el("p", claim.text + " " + claim.citations.join(" ")));
  const expert = expertResults(answer, { read: readExpertMedia });
  if (expert) parent.append(expert);
  if (answer.model_supplement) {
    parent.append(
      el("h4", uiText("模型一般知识补充（非知识库证据）")),
      el("p", answer.model_supplement),
    );
  }
  for (const source of answer.sources) {
    const details = el("details", null, "technical-details");
    details.append(
      el(
        "summary",
        joinText([
          "",
          source.citation,
          " ",
          source.title,
          " · ",
          source.note_path,
          ":",
          source.line,
          "–",
          source.line_end,
          "",
        ]),
      ),
    );
    details.append(el("p", source.source_label), el("pre", source.snippet));
    if (source.artifact_stage || source.review_state)
      details.append(
        el(
          "p",
          phrase("产物阶段：{p0} · 审核：{p1}", {
            p0: source.artifact_stage || "未知",
            p1: source.review_state || "未知",
          }),
        ),
      );
    parent.append(details);
  }
  const summary = operation.result;
  parent.append(
    el(
      "p",
      phrase("生成调用 {p0} 次 · {p1} 秒 · 用量{p2}", {
        p0: summary.provider_calls,
        p1: summary.elapsed_seconds,
        p2:
          summary.usage_state === "reported"
            ? uiText("已报告")
            : uiText("未知（不重试）"),
      }),
      "muted",
    ),
  );
  if (summary.trace) showJSON(parent, summary.trace, "本轮实际调用顺序");
  if (summary.usage) showJSON(parent, summary.usage, "模型用量");
  if (Number.isSafeInteger(summary.cloud_cost_nusd))
    parent.append(
      el(
        "p",
        phrase("云端费用：{p0} USD（按配置单价核算）。", {
          p0: (summary.cloud_cost_nusd / 1e9).toFixed(6),
        }),
        "muted",
      ),
    );
  else if (summary.provider_calls)
    parent.append(
      el(
        "p",
        uiText("模型费用未知；Tokens 如有报告已计入该 Key 预算。"),
        "muted",
      ),
    );
}
