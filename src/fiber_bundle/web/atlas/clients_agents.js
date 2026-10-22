// Agent connections: independent read-only connections (Hermes or generic MCP). Saving
// pauses access; applying config, enabling queries, rotating and revoking are explicit.
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  checkbox,
  codeBlock,
  disclosure,
  empty,
  failure,
  field,
  input,
  listRow,
  notice,
  select,
  skeleton,
  textarea,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";

const t = strings({
  zh: {
    list: "Agent 连接",
    new: "新建连接",
    none: "尚未创建 Agent 连接。",
    newTitle: "新建 Agent 连接",
    intro: "独立只读连接；先查询当前资料，再由受管模型生成引用草稿。",
    name: "连接名称",
    type: "Agent 类型",
    hermes: "本机 Hermes",
    mcp: "通用 MCP 客户端",
    hermesFound: "已识别 Hermes {v}，实际接入仍需检查。",
    hermesMissing: "Hermes 未找到或版本不兼容；可创建通用 MCP 连接。",
    vaults: "明确授权知识库（仅查询）",
    noVaults: "暂无可授权知识库。",
    policy: "回答规则",
    augmented: "知识库优先，可补充一般知识",
    evidence_only: "仅依据知识库",
    model: "受管模型配置",
    pickModel: "请选择已配置模型",
    local: "本机",
    modelNote:
      "Hermes 将问题与所选库的检索片段发送到选定本机模型；备用不启动。通用 MCP 的模型与费用由外部宿主控制。",
    limits: "检索与模型限额",
    queries: "每题最多检索次数",
    results_per_query: "每次最多命中",
    evidence_chars: "总证据字符",
    prompt_bytes: "完整提示字节",
    timeout_seconds: "硬截止（秒）",
    output_tokens: "最多输出 tokens",
    limitsNote:
      "每题最多一次生成，未知用量不重试；每连接每天最多 10 次提交，且受模型配置额度约束。",
    saveNew: "保存（暂停状态）",
    save: "保存并暂停",
    needVault: "请明确选择至少一个知识库。",
    needModel: "请先在“模型与预算”配置本机模型，再选择此配置。",
    saved: "连接已保存并暂停。应用配置后可授权查询。",
    revoked: "连接已永久撤销。可新建连接，不能恢复此凭证。",
    saveFirst: "请先保存连接配置，再执行此操作。",
    notActionable: "此连接不可操作。",
    operations: "操作",
    apply: "应用接入配置",
    applyTitle: "应用接入配置？",
    applyHermes:
      "将为此连接创建独立 Hermes 配置和 MCP 入口，不修改现有飞书 gateway。",
    applyMcp: "将创建只读 MCP 入口；外部宿主仍需自行导入配置。",
    enable: "授权并启用查询",
    pause: "暂停查询",
    paused: "查询授权已暂停；后台拒绝后续检索。",
    enabled: "已授予所选库查询权限，开始无模型连接检查。",
    probe: "验证连接（无模型）",
    export: "导出 MCP 配置",
    exportNote:
      "以下配置无明文凭证；在同一台机器的外部 MCP 客户端导入后再核对。",
    credentials: "凭证与撤销",
    rotate: "轮换凭证",
    rotateTitle: "轮换凭证？",
    rotateBody: "旧凭证将永久失效，新凭证初始暂停，需重新应用并授权。",
    revoke: "永久撤销连接",
    revokeTitle: "永久撤销此连接全部访问？",
    revokeBody: "已交付外部 Agent 的内容无法收回。",
    revokedDone: "连接已撤销。",
    mcpNote:
      "通用 MCP 仅验证三个只读工具。模型回答、强制检索与费用由外部客户端控制；导出不代表宿主已加载。",
    test: "受管问答测试",
    testScope:
      "范围：{vaults} · 模型 {model} · {endpoint} · 一次生成 · 最多 {s} 秒。测试不会保存为知识。",
    pending: "待配置",
    question: "测试问题",
    terms: "本轮检索词",
    termsHelp: "每行一次查询，最多两行；空格连接的词须同时命中。",
    suggest: "从问题建议检索词（本机，无模型）",
    noTerms: "无法可靠选词，请填写明确术语、型号或缩短问题。",
    termsFilled: "已填入候选检索词，请核对后再测试。",
    runTest: "执行 Hermes 问答测试",
    badTerms: "请填写限额内的字面检索词。",
    result: "最近操作结果",
    cancelOp: "取消受管操作",
    cancelled: "已请求取消，后台将停止本应用拥有的进程。",
    expired: "观察已到期。后台仍有硬截止；可手动刷新核对。",
    opDone: "操作已完成。配置、工具可用和问答结果分别显示。",
    opFailed: "操作未完成，请查看本次安全错误。",
    history: "操作记录",
    loadHistory: "读取操作记录",
    page: "第 {n} 页 · 安全摘要",
    prev: "上一页",
    next: "下一页",
    a_paused: "访问已暂停",
    a_enabled: "查询已授权",
    a_revoked: "已永久撤销",
    a_repair_required: "访问需修复",
    c_applied: "配置已应用",
    c_pending: "配置待应用",
    c_outdated: "配置需重新应用",
    c_repair_required: "配置需修复",
    c_external_configuration_required: "MCP 配置已生成 · 待外部导入",
    probePassed: "MCP 工具可用",
    probeState: "连接检查：{s}",
    testPassed: "先检索后回答已验证",
    testState: "问答测试：{s}",
    k_not_run: "未运行",
    k_outdated: "已过期",
    k_failed: "失败",
    k_cancelled: "已取消",
    k_passed: "通过",
    opLine: "操作：{kind} · {state}",
    opError: "操作未完成：{code}。可核对配置或手动重试新任务。",
    outdated: "结果已过期：当前权限、配置或来源已变化。请显式重新测试。",
    memory: "本机模型内存不足，请释放内存后显式提交新测试。",
    usageUnknown: "本次模型用量未知，系统不会自动重试。",
    probed: "已通过实际 stdio 工具发现及范围检查，没有调用模型。",
    i_ready: "索引就绪",
    i_absent: "索引未建立：检索使用当前只读快照",
    i_stale: "索引已过期：请在知识库页显式更新后再问答",
    answer: "回答草稿 · 尚未进行语义核实",
    retrieval: "检索词：{q} · 来源 {n} 条",
    noEvidence: "知识库证据不足，未生成知识库事实。",
    supplement: "模型一般知识补充（非知识库证据）",
    stage: "产物阶段：{a} · 审核：{r}",
    unknown: "未知",
    calls: "生成调用 {n} 次 · {s} 秒 · 用量{u}",
    reported: "已报告",
    notReported: "未知（不重试）",
    trace: "本轮实际调用顺序",
    usage: "模型用量",
    discard: "当前有未保存的内容。确定放弃这些编辑？",
  },
  en: {
    list: "Agent connections",
    new: "New connection",
    none: "No agent connections yet.",
    newTitle: "New agent connection",
    intro:
      "Independent read-only connections: they query current material first, then a managed model drafts a cited answer.",
    name: "Connection name",
    type: "Agent type",
    hermes: "Local Hermes",
    mcp: "Generic MCP client",
    hermesFound:
      "Hermes {v} detected; the actual connection still needs checking.",
    hermesMissing:
      "Hermes not found or incompatible; you can create a generic MCP connection.",
    vaults: "Vaults to authorize (query only)",
    noVaults: "No vaults available to authorize.",
    policy: "Answer policy",
    augmented: "Vault first; may add general knowledge",
    evidence_only: "Vault evidence only",
    model: "Managed model configuration",
    pickModel: "Choose a configured model",
    local: "local",
    modelNote:
      "Hermes sends the question plus retrieved snippets to the chosen local model; backups never start. For generic MCP, the host controls the model and cost.",
    limits: "Retrieval and model limits",
    queries: "Max searches per question",
    results_per_query: "Max hits per search",
    evidence_chars: "Total evidence characters",
    prompt_bytes: "Full prompt bytes",
    timeout_seconds: "Hard deadline (s)",
    output_tokens: "Max output tokens",
    limitsNote:
      "One generation per question; unknown usage is never retried. Up to 10 submissions per connection per day, within the model budget.",
    saveNew: "Save (paused)",
    save: "Save and pause",
    needVault: "Select at least one vault explicitly.",
    needModel:
      "Configure a local model under Models & budget first, then select it.",
    saved:
      "Connection saved and paused. Apply the configuration, then authorize queries.",
    revoked:
      "This connection is permanently revoked. Create a new one; the credential can't be restored.",
    saveFirst: "Save the connection configuration first.",
    notActionable: "This connection can't be operated.",
    operations: "Actions",
    apply: "Apply configuration",
    applyTitle: "Apply the connection configuration?",
    applyHermes:
      "Creates a separate Hermes configuration and MCP entry for this connection; the existing Feishu gateway is untouched.",
    applyMcp:
      "Creates a read-only MCP entry; the external host still has to import it.",
    enable: "Authorize and enable queries",
    pause: "Pause queries",
    paused: "Query access paused; the backend rejects further searches.",
    enabled:
      "Query access granted on the selected vaults; running a model-free connection check.",
    probe: "Check connection (no model)",
    export: "Export MCP configuration",
    exportNote:
      "This configuration contains no plaintext credential. Import it into an MCP client on this machine, then verify.",
    credentials: "Credential and revocation",
    rotate: "Rotate credential",
    rotateTitle: "Rotate the credential?",
    rotateBody:
      "The old credential stops working permanently; the new one starts paused and must be applied and authorized again.",
    revoke: "Revoke connection permanently",
    revokeTitle: "Permanently revoke all access for this connection?",
    revokeBody:
      "Content already delivered to the external agent can't be recalled.",
    revokedDone: "Connection revoked.",
    mcpNote:
      "For generic MCP only the three read-only tools are verified. Answers, forced retrieval and cost are controlled by the external client; exporting doesn't mean the host loaded it.",
    test: "Managed Q&A test",
    testScope:
      "Scope: {vaults} · model {model} · {endpoint} · one generation · up to {s} s. Tests are never saved as knowledge.",
    pending: "not configured",
    question: "Test question",
    terms: "Retrieval terms for this round",
    termsHelp:
      "One search per line, up to two lines; space-separated words must all match.",
    suggest: "Suggest terms from the question (local, no model)",
    noTerms:
      "Couldn't pick terms reliably. Use explicit terms or model numbers, or shorten the question.",
    termsFilled: "Candidate terms filled in. Check them before testing.",
    runTest: "Run Hermes Q&A test",
    badTerms: "Enter literal retrieval terms within the limits.",
    result: "Latest operation result",
    cancelOp: "Cancel managed operation",
    cancelled:
      "Cancellation requested; the backend stops processes this app owns.",
    expired:
      "Watching expired. The backend still has a hard deadline; refresh to check.",
    opDone:
      "Operation finished. Configuration, tool availability and answers are shown separately.",
    opFailed: "The operation didn't complete. See the safe error below.",
    history: "Operation history",
    loadHistory: "Load operation history",
    page: "Page {n} · safe summary",
    prev: "Previous",
    next: "Next",
    a_paused: "Access paused",
    a_enabled: "Queries authorized",
    a_revoked: "Revoked",
    a_repair_required: "Access needs repair",
    c_applied: "Configuration applied",
    c_pending: "Configuration pending",
    c_outdated: "Re-apply configuration",
    c_repair_required: "Configuration needs repair",
    c_external_configuration_required:
      "MCP config generated · import externally",
    probePassed: "MCP tools available",
    probeState: "Connection check: {s}",
    testPassed: "Retrieve-then-answer verified",
    testState: "Q&A test: {s}",
    k_not_run: "not run",
    k_outdated: "outdated",
    k_failed: "failed",
    k_cancelled: "cancelled",
    k_passed: "passed",
    opLine: "Operation: {kind} · {state}",
    opError:
      "Operation didn't complete: {code}. Check the configuration or start a new run.",
    outdated:
      "Result outdated: permissions, configuration or sources changed. Re-test explicitly.",
    memory:
      "The local model ran out of memory. Free memory, then submit a new test.",
    usageUnknown:
      "Model usage for this run is unknown; it won't be retried automatically.",
    probed:
      "Verified via real stdio tool discovery and scope checks; no model was called.",
    i_ready: "Index ready",
    i_absent: "No index: retrieval uses the current read-only snapshot",
    i_stale: "Index stale: update it on the Vaults page before asking",
    answer: "Answer draft · not semantically verified",
    retrieval: "Terms: {q} · {n} sources",
    noEvidence: "Not enough vault evidence; no vault facts were generated.",
    supplement: "Model general-knowledge supplement (not vault evidence)",
    stage: "Artifact stage: {a} · review: {r}",
    unknown: "unknown",
    calls: "{n} generation calls · {s} s · usage {u}",
    reported: "reported",
    notReported: "unknown (no retry)",
    trace: "Actual call order",
    usage: "Model usage",
    discard: "There are unsaved changes. Discard them?",
  },
});
const LIMITS = [
  ["queries", [1, 2]],
  ["results_per_query", [1, 5]],
  ["evidence_chars", [400, 16000]],
  ["prompt_bytes", [4096, 65536]],
  ["timeout_seconds", [1, 180]],
  ["output_tokens", [128, 4096]],
];
// Bounded literal-term suggestions; no network or model.
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
export function stateBadges(row) {
  const check = (s) => (t("k_" + s) === "k_" + s ? s : t("k_" + s));
  return [
    badge(
      t("a_" + row.access_state),
      {
        enabled: "success",
        paused: "paused",
        revoked: "info",
        repair_required: "error",
      }[row.access_state] || "info",
    ),
    row.configuration_state
      ? badge(
          t("c_" + row.configuration_state),
          row.configuration_state === "applied" ? "success" : "waiting",
        )
      : null,
    badge(
      row.probe.state === "passed"
        ? t("probePassed")
        : t("probeState", { s: check(row.probe.state) }),
      row.probe.state === "passed"
        ? "success"
        : row.probe.state === "failed"
          ? "error"
          : "idle",
    ),
    badge(
      row.test.state === "passed"
        ? t("testPassed")
        : t("testState", { s: check(row.test.state) }),
      row.test.state === "passed"
        ? "success"
        : row.test.state === "failed"
          ? "error"
          : "idle",
    ),
  ];
}
function operationView(operation) {
  const parts = [
    h(
      "p.a-subtle",
      t("opLine", { kind: operation.kind, state: operation.state }),
    ),
  ];
  if (operation.error_code)
    parts.push(notice(t("opError", { code: operation.error_code }), "error"));
  const r = operation.result;
  if (r?.state === "outdated") parts.push(notice(t("outdated"), "warning"));
  if (r?.provider_error === "agent_provider_http_507")
    parts.push(notice(t("memory"), "warning"));
  if (r?.usage_state === "unknown")
    parts.push(h("p.a-subtle", t("usageUnknown")));
  if (r?.indices)
    parts.push(
      h("p", t("probed")),
      h(
        "ul.a-plain-list",
        r.indices.map((i) =>
          h(
            "li",
            t("i_" + i.state) === "i_" + i.state ? i.state : t("i_" + i.state),
          ),
        ),
      ),
    );
  const answer = operation.answer;
  if (answer) {
    parts.push(
      h("h4", t("answer")),
      h(
        "p.a-subtle",
        t("retrieval", {
          q: answer.retrieval_queries.join("；"),
          n: answer.sources.length,
        }),
      ),
      answer.claims.length
        ? h(
            "ul.a-plain-list",
            answer.claims.map((c) =>
              h("li", `${c.text} ${c.citations.join(" ")}`),
            ),
          )
        : notice(t("noEvidence"), "info"),
      answer.model_supplement
        ? notice(
            h(
              "div",
              h("strong", t("supplement")),
              h("p", answer.model_supplement),
            ),
            "warning",
          )
        : null,
      ...answer.sources.map((s) =>
        disclosure(
          `${s.citation} ${s.title} · ${s.note_path}:${s.line}–${s.line_end}`,
          h("p.a-subtle", s.source_label),
          h("pre.a-pre", s.snippet),
          s.artifact_stage || s.review_state
            ? h(
                "p.a-subtle",
                t("stage", {
                  a: s.artifact_stage || t("unknown"),
                  r: s.review_state || t("unknown"),
                }),
              )
            : null,
        ),
      ),
      h(
        "p.a-subtle",
        t("calls", {
          n: r.provider_calls,
          s: r.elapsed_seconds,
          u: r.usage_state === "reported" ? t("reported") : t("notReported"),
        }),
      ),
      r.trace
        ? disclosure(t("trace"), codeBlock(JSON.stringify(r.trace, null, 2)))
        : null,
      r.usage
        ? disclosure(t("usage"), codeBlock(JSON.stringify(r.usage, null, 2)))
        : null,
    );
  }
  return parts;
}

export function createAgents(ctx, params) {
  let rows = [],
    vaults = [],
    catalog = null,
    selected = params.id || null,
    epoch = 0,
    timer = null,
    active = null,
    form = null;
  const listHost = h("div.a-rows");
  const detail = h("div.a-stack");
  const resultHost = h("div.a-stack", { "aria-live": "polite" });
  const current = () => rows.find((r) => r.id === selected);
  const changeBody = (item) => ({
    expected_revision: item.revision,
    request_id: crypto.randomUUID(),
  });
  function stop() {
    clearTimeout(timer);
    timer = null;
    epoch++;
  }
  function canAct() {
    if (form && hasUnsavedChanges(form)) throw new Error(t("saveFirst"));
    const item = current();
    if (!item || item.access_state === "revoked")
      throw new Error(t("notActionable"));
    return item;
  }
  function drawList() {
    clear(
      listHost,
      rows.length
        ? rows.map((item) =>
            listRow({
              iconName:
                item.configuration.agent_type === "hermes" ? "spark" : "link",
              title: item.configuration.name,
              detail:
                item.configuration.agent_type === "hermes"
                  ? t("hermes")
                  : t("mcp"),
              status: [
                t("a_" + item.access_state),
                {
                  enabled: "success",
                  paused: "paused",
                  revoked: "info",
                  repair_required: "error",
                }[item.access_state] || "info",
              ],
              selected: selected === item.id,
              onClick: () => choose(item.id),
            }),
          )
        : empty(t("none"), { iconName: "clients" }),
    );
  }
  function choose(id) {
    if (hasUnsavedChanges(detail) && !window.confirm(t("discard"))) return;
    stop();
    active = null;
    selected = id;
    drawList();
    drawDetail();
  }
  function connectionForm(configuration) {
    const value = configuration || {
      name: "",
      agent_type: "hermes",
      vault_ids: [],
      answer_policy: "augmented",
      model_configuration_id: catalog.models[0]?.id || "",
      limits: {
        queries: 2,
        results_per_query: 5,
        evidence_chars: 8000,
        prompt_bytes: 32768,
        timeout_seconds: 60,
        output_tokens: 1024,
      },
    };
    const name = input({
      name: "name",
      required: true,
      maxLength: 100,
      value: value.name,
    });
    const type = select(
      [
        ["hermes", t("hermes")],
        ["mcp", t("mcp")],
      ],
      value.agent_type,
      { name: "type" },
    );
    const policy = select(
      [
        ["augmented", t("augmented")],
        ["evidence_only", t("evidence_only")],
      ],
      value.answer_policy,
      { name: "policy" },
    );
    const model = select(
      [
        ["", t("pickModel")],
        ...catalog.models.map((m) => [m.id, `${m.name} · ${t("local")}`]),
      ],
      value.model_configuration_id || "",
      { name: "model" },
    );
    const boxes = vaults
      .filter((v) => v.status === "active" && v.attachment_state !== "detached")
      .map((v) =>
        checkbox(v.name, value.vault_ids.includes(v.id), {
          name: "vault_ids",
          value: v.id,
        }),
      );
    const limits = Object.fromEntries(
      LIMITS.map(([key, [min, max]]) => [
        key,
        input({
          type: "number",
          name: key,
          required: true,
          min,
          max,
          value: value.limits[key],
        }),
      ]),
    );
    const layout = () => (model.disabled = type.value === "mcp");
    type.addEventListener("change", layout);
    layout();
    const submit = h(
      "button.a-button.is-primary",
      { type: "submit" },
      configuration ? t("save") : t("saveNew"),
    );
    const node = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      h(
        "div.a-form-grid",
        field(t("name"), name),
        field(t("type"), type, {
          help:
            catalog.hermes.state === "candidate"
              ? t("hermesFound", { v: catalog.hermes.version })
              : t("hermesMissing"),
        }),
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("vaults")),
        boxes.length
          ? h(
              "div.a-toolbar",
              boxes.map((b) => b.label),
            )
          : h("p.a-subtle", t("noVaults")),
      ),
      h(
        "div.a-form-grid",
        field(t("policy"), policy),
        field(t("model"), model, { help: t("modelNote") }),
      ),
      disclosure(
        t("limits"),
        h(
          "div.a-form-grid",
          LIMITS.map(([key]) => field(t(key), limits[key])),
        ),
        h("p.a-subtle", t("limitsNote")),
      ),
      h("div.a-form-actions", submit),
    );
    node.read = () => {
      const vault_ids = boxes
        .filter((b) => b.input.checked)
        .map((b) => b.input.value);
      if (!vault_ids.length) throw new Error(t("needVault"));
      if (type.value === "hermes" && !model.value)
        throw new Error(t("needModel"));
      return {
        schema_version: "fiber-agent-connection/v1",
        name: name.value,
        agent_type: type.value,
        vault_ids,
        answer_policy: policy.value,
        model_configuration_id: type.value === "hermes" ? model.value : null,
        limits: {
          ...Object.fromEntries(
            LIMITS.map(([key]) => [key, Number(limits[key].value)]),
          ),
          provider_calls: 1,
        },
      };
    };
    node.submit_ = submit;
    rememberDraft(node);
    return node;
  }
  function drawDetail() {
    clear(resultHost);
    if (!catalog) return;
    const item = current();
    form = connectionForm(item?.configuration);
    const statusHost = h("div.a-toolbar", item ? stateBadges(item) : null);
    const sections = [
      card(
        {
          title: item ? item.configuration.name : t("newTitle"),
          subtitle: t("intro"),
          actions: statusHost,
        },
        form,
      ),
    ];
    if (item?.access_state === "revoked") {
      for (const control of form.querySelectorAll("input,select,button"))
        control.disabled = true;
      sections.push(notice(t("revoked"), "info"));
      clear(detail, ...sections);
      return;
    }
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const submitted = form;
      void run(submitted.submit_, async () => {
        const acknowledge = captureDraftSave(submitted),
          generation = epoch;
        const configuration = submitted.read(),
          existing = current();
        const saved = existing
          ? await ctx.api.agents.save(existing.id, {
              ...changeBody(existing),
              configuration,
            })
          : await ctx.api.agents.create({
              request_id: crypto.randomUUID(),
              configuration,
            });
        if (generation !== epoch) return;
        acknowledge();
        rows = [saved, ...rows.filter((r) => r.id !== saved.id)];
        selected = saved.id;
        ctx.store.invalidate("agents", "clients");
        stop();
        active = null;
        drawList();
        drawDetail();
        toast(t("saved"), { tone: "success" });
      });
    });
    if (item) {
      const act = (label, work, variant, iconName) => {
        const control = button(label, { variant, iconName });
        control.addEventListener("click", () => run(control, work));
        return control;
      };
      const toggle = act(
        item.access_state === "enabled" ? t("pause") : t("enable"),
        async () => {
          const target = canAct(),
            action = target.access_state === "enabled" ? "pause" : "enable";
          const saved = await ctx.api.agents.control(
            target.id,
            action,
            changeBody(target),
          );
          rows = rows.map((r) => (r.id === saved.id ? saved : r));
          drawList();
          clear(statusHost, stateBadges(saved));
          toggle.textContent =
            saved.access_state === "enabled" ? t("pause") : t("enable");
          probe.disabled = saved.access_state !== "enabled";
          toast(action === "pause" ? t("paused") : t("enabled"), {
            tone: "success",
          });
          if (action === "enable") await start("probe");
        },
        item.access_state === "enabled" ? null : "primary",
        item.access_state === "enabled" ? "stop" : "check",
      );
      const probe = act(t("probe"), () => start("probe"), null, "pulse");
      probe.disabled = item.access_state !== "enabled";
      sections.push(
        card(
          { title: t("operations") },
          h(
            "div.a-form-actions",
            act(
              t("apply"),
              async () => {
                const target = canAct();
                const ok = await confirm({
                  title: t("applyTitle"),
                  body:
                    target.configuration.agent_type === "hermes"
                      ? t("applyHermes")
                      : t("applyMcp"),
                  confirmLabel: t("apply"),
                });
                if (!ok) return false;
                await start("apply_config");
              },
              null,
              "upload",
            ),
            toggle,
            probe,
            act(
              t("export"),
              async () => {
                canAct();
                const value = await ctx.api.agents.export(item.id);
                clear(
                  resultHost,
                  notice(t("exportNote"), "info"),
                  codeBlock(JSON.stringify(value, null, 2)),
                );
              },
              null,
              "download",
            ),
          ),
          disclosure(
            t("credentials"),
            h(
              "div.a-form-actions",
              act(
                t("rotate"),
                async () => {
                  canAct();
                  if (
                    !(await confirm({
                      title: t("rotateTitle"),
                      body: t("rotateBody"),
                      confirmLabel: t("rotate"),
                      tone: "danger",
                    }))
                  )
                    return false;
                  await start("rotate_credential");
                },
                "danger-outline",
                "key",
              ),
              act(
                t("revoke"),
                async () => {
                  const target = canAct();
                  if (
                    !(await confirm({
                      title: t("revokeTitle"),
                      body: t("revokeBody"),
                      confirmLabel: t("revoke"),
                      tone: "danger",
                    }))
                  )
                    return false;
                  await ctx.api.agents.control(
                    target.id,
                    "revoke",
                    changeBody(target),
                  );
                  ctx.store.invalidate("agents", "clients");
                  await refresh();
                  toast(t("revokedDone"), { tone: "success" });
                },
                "danger-outline",
                "trash",
              ),
            ),
          ),
        ),
      );
      sections.push(
        item.configuration.agent_type === "hermes"
          ? testCard(item)
          : notice(t("mcpNote"), "info"),
      );
      sections.push(
        card({ title: t("result") }, resultHost),
        historyCard(item),
      );
    }
    clear(detail, ...sections);
  }
  function testCard(item) {
    const model = catalog.models.find(
      (m) => m.id === item.configuration.model_configuration_id,
    );
    const question = textarea({
      id: "agent-question",
      required: true,
      maxLength: 2000,
      rows: 3,
    });
    const terms = textarea({
      id: "agent-test-queries",
      required: true,
      maxLength: 401,
      rows: 2,
    });
    const suggest = button(t("suggest"), {
      variant: "small",
      iconName: "spark",
    });
    suggest.addEventListener("click", () => {
      const found = suggestTerms(question.value);
      if (!found.length) {
        toast(t("noTerms"), { tone: "warning" });
        return;
      }
      terms.value = found
        .slice(0, (current() || item).configuration.limits.queries)
        .join("\n");
      terms.dispatchEvent(new window.Event("input", { bubbles: true }));
      toast(t("termsFilled"), { tone: "info" });
    });
    const submit = h(
      "button.a-button.is-primary",
      { type: "submit", disabled: item.access_state !== "enabled" },
      icon("play"),
      t("runTest"),
    );
    const testForm = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      field(t("question"), question),
      h(
        "div.a-field-row",
        field(t("terms"), terms, { help: t("termsHelp") }),
        suggest,
      ),
      h("div.a-form-actions", submit),
    );
    testForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(submit, async () => {
        const acknowledge = captureDraftSave(testForm);
        const queries = terms.value
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean);
        if (
          !queries.length ||
          queries.length > (current() || item).configuration.limits.queries ||
          queries.some((s) => s.length > 200 || s.split(/\s+/).length > 8)
        )
          throw new Error(t("badTerms"));
        await start("test_query", {
          request_id: crypto.randomUUID(),
          question: question.value,
          retrieval_queries: queries,
        });
        acknowledge();
      });
    });
    const scope = t("testScope", {
      vaults: item.configuration.vault_ids
        .map((id) => vaults.find((v) => v.id === id)?.name || id)
        .join("、"),
      model: model?.name || t("pending"),
      endpoint: model?.endpoint || t("local"),
      s: item.configuration.limits.timeout_seconds,
    });
    return card({ title: t("test"), subtitle: scope }, testForm);
  }
  function historyCard(item) {
    const body = h("div.a-stack");
    let offset = 0;
    async function page(next) {
      const generation = epoch;
      const records = await ctx.api.agents.history(item.id, next);
      if (generation !== epoch || selected !== item.id) return;
      offset = next;
      clear(
        body,
        h("p.a-subtle", t("page", { n: Math.floor(offset / 50) + 1 })),
        codeBlock(JSON.stringify(records, null, 2)),
        h(
          "div.a-toolbar",
          button(t("prev"), {
            variant: "small",
            disabled: offset === 0,
            onClick: () => page(Math.max(0, offset - 50)),
          }),
          button(t("next"), {
            variant: "small",
            disabled: records.length < 50 || offset >= 9950,
            onClick: () => page(offset + 50),
          }),
        ),
      );
    }
    const load = button(t("loadHistory"), {
      variant: "small",
      iconName: "clock",
    });
    load.addEventListener("click", () => run(load, () => page(0)));
    return disclosure(t("history"), load, body);
  }
  function showOperation(operation) {
    const cancel =
      ["queued", "running"].includes(operation.state) && active
        ? button(t("cancelOp"), {
            variant: "small",
            onClick: async () => {
              const item = current();
              if (!item || !active) return;
              try {
                await ctx.api.agents.cancel(
                  item.id,
                  active.operation,
                  changeBody(item),
                );
                toast(t("cancelled"), { tone: "info" });
              } catch (error) {
                toast(error.message, { tone: "error" });
              }
            },
          })
        : null;
    clear(resultHost, ...operationView(operation), cancel);
  }
  async function start(kind, request) {
    const item = canAct(),
      generation = epoch;
    const operation = await ctx.api.agents.start(item.id, {
      ...changeBody(item),
      kind,
      ...(request ? { request } : {}),
    });
    if (generation !== epoch) return;
    active = {
      identity: item.id,
      operation: operation.id,
      deadline: Date.now() + 185000,
    };
    showOperation(operation);
    if (["queued", "running"].includes(operation.state)) observe(generation);
    else await finish(operation, generation);
  }
  async function observe(generation) {
    if (!active || generation !== epoch) return;
    if (Date.now() >= active.deadline) {
      stop();
      toast(t("expired"), { tone: "warning" });
      return;
    }
    try {
      const operation = await ctx.api.agents.status(
        active.identity,
        active.operation,
      );
      if (generation !== epoch) return;
      showOperation(operation);
      if (["queued", "running"].includes(operation.state))
        timer = setTimeout(() => observe(generation), 800);
      else await finish(operation, generation);
    } catch (error) {
      if (generation === epoch) {
        stop();
        toast(error.message, { tone: "error" });
      }
    }
  }
  async function finish(operation, generation) {
    if (generation !== epoch) return;
    clearTimeout(timer);
    timer = null;
    const item = await ctx.api.agents.detail(selected);
    if (generation !== epoch) return;
    rows = rows.map((r) => (r.id === item.id ? item : r));
    drawList();
    const badges = detail.querySelector(".a-card-actions .a-toolbar");
    if (badges) clear(badges, stateBadges(item));
    showOperation(operation);
    toast(operation.state === "succeeded" ? t("opDone") : t("opFailed"), {
      tone: operation.state === "succeeded" ? "success" : "error",
    });
  }
  async function refresh() {
    stop();
    const generation = epoch;
    if (!rows.length) clear(listHost, skeleton(3));
    let connections, available, choices;
    try {
      [connections, available, choices] = await Promise.all([
        ctx.api.agents.list(),
        ctx.store.vaults({ force: true }),
        ctx.api.agents.catalog(),
      ]);
    } catch (error) {
      clear(listHost, failure(error, refresh));
      return;
    }
    if (generation !== epoch) return;
    rows = connections;
    vaults = available;
    catalog = choices;
    if (selected && !current()) selected = null;
    drawList();
    drawDetail();
    if (selected) {
      const operations = await ctx.api.agents.history(selected);
      if (generation !== epoch) return;
      const latest = operations.find((op) =>
        ["test_query", "probe"].includes(op.kind),
      );
      if (latest) {
        active = {
          identity: selected,
          operation: latest.id,
          deadline: Date.now() + 185000,
        };
        const operation = await ctx.api.agents.status(selected, latest.id);
        if (generation !== epoch) return;
        showOperation(operation);
        if (["queued", "running"].includes(operation.state))
          observe(generation);
      }
    }
  }
  const newButton = button(t("new"), {
    variant: "primary",
    iconName: "plus",
    onClick: () => choose(null),
  });
  return {
    node: h(
      "div.a-split",
      h(
        "div.a-split-list",
        card(
          { title: t("list"), className: "is-flush", actions: newButton },
          listHost,
        ),
      ),
      detail,
    ),
    refresh,
    dirty: () => hasUnsavedChanges(detail),
    dispose: stop,
  };
}
