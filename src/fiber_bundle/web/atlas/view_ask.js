// Ask & search. Note search returns snippets and locations only (no model, no edits).
// Model queries are queued, then each call is approved and launched explicitly.
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  checkbox,
  disclosure,
  empty,
  failure,
  field,
  input,
  notice,
  select,
  setOptions,
  skeleton,
  tabs,
  textarea,
  facts,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { captureDraftSave, rememberDraft } from "../unsaved_changes.js";
import { shortId, usd } from "./format.js";

const t = strings({
  zh: {
    search: "检索笔记",
    ask: "向模型提问",
    identity: "以此身份检索",
    noIdentity: "没有具有查询权限的身份。请先在 Agent 与客户端授予查询权限。",
    noFeature: "当前后台未提供笔记搜索接口。",
    placeholder: "输入关键词，例如“冷板 压降”",
    go: "检索",
    searchNote:
      "以所选身份的查询权限检索其默认查询库；只显示命中片段与位置，不修改笔记、不调用模型。",
    hits: "在 {v} 个知识库中找到 {n} 条命中。",
    noHits: "该身份的默认查询库中没有命中。",
    noDefault:
      "该身份没有默认参与查询的知识库。可在知识库页面打开“默认参与查询”。",
    line: "第 {n} 行",
    askThis: "就此提问",
    question: "问题",
    retrieval: "检索词",
    retrievalHelp: "用于在知识库中检索证据的关键词。",
    submit: "加入待执行队列",
    needPolicy: "请先在“模型与预算”保存模型配置。",
    queued: "查询已入队，请在下方任务中明确批准并启动。",
    askNote:
      "提交只会排队；每次模型调用都需在任务上明确批准。云端仅收到答案及其中引文。",
    tasks: "查询任务",
    none: "暂无模型查询任务。",
    local: "本地查询",
    cloud: "云端整理",
    st_queued: "待执行",
    st_reserved: "已入队",
    st_running: "执行中",
    st_succeeded: "完成",
    st_failed: "失败",
    st_cancelled: "已取消",
    st_interrupted: "已中断",
    st_waiting_budget: "预算不足",
    job: "有限执行：{state}",
    approve: "批准此任务的一次模型调用",
    launch: "启动一次",
    needApprove: "请先批准此次模型调用。",
    cancelQueued: "取消待执行任务",
    cancelJob: "取消任务",
    recover: "核对中断状态",
    answer: "查看答案与引文",
    hideAnswer: "收起答案",
    draft: "云端未核验草稿",
    quotes: "引文",
    claims: "要点",
    cautions: "注意",
    prepareCloud: "准备云端整理",
    cloudPrepared: "云端整理任务已准备，需另行批准启动。",
    backups: "备用模型",
    loadBackups: "读取此任务的备用列表",
    noBackups: "此任务配置没有声明备用模型。",
    backupOption: "{model} · 任务上限 {limit}",
    approveBackup: "明确批准声明中的此备用模型",
    selectBackup: "批准备用选择",
    needBackup: "请读取备用列表并明确批准此次选择。",
    audit: "费用与审计",
    loadAudit: "查看调用费用与审计",
    costState: "费用状态",
    reserved: "预留",
    charged: "结算",
    unknown: "未知",
    localCall: "本地调用",
    tokens: "输入 {i} / 输出 {o} token",
    selection: "备用选择 {n}",
    noCalls: "尚无模型调用记录。",
    models: "模型与预算",
  },
  en: {
    search: "Search notes",
    ask: "Ask a model",
    identity: "Search as",
    noIdentity:
      "No identity has query access. Grant it in Agents & clients first.",
    noFeature: "The backend doesn't offer note search.",
    placeholder: "Type keywords, e.g. “cold plate pressure drop”",
    go: "Search",
    searchNote:
      "Searches the identity's default query vaults with its grants. Shows matching snippets and locations only — never edits notes or calls a model.",
    hits: "{n} matches in {v} vaults.",
    noHits: "No matches in this identity's default query vaults.",
    noDefault:
      "This identity has no default query vault. Turn on “Use in default queries” on a vault.",
    line: "line {n}",
    askThis: "Ask about this",
    question: "Question",
    retrieval: "Retrieval keywords",
    retrievalHelp: "Keywords used to find evidence in the vaults.",
    submit: "Add to queue",
    needPolicy: "Save a model configuration under Models & budget first.",
    queued:
      "Query queued. Approve and launch it explicitly in the task list below.",
    askNote:
      "Submitting only queues the query; every model call must be approved on its task. The cloud only receives the answer and its quotations.",
    tasks: "Query tasks",
    none: "No model query tasks yet.",
    local: "Local query",
    cloud: "Cloud editing",
    st_queued: "Queued",
    st_reserved: "Reserved",
    st_running: "Running",
    st_succeeded: "Done",
    st_failed: "Failed",
    st_cancelled: "Cancelled",
    st_interrupted: "Interrupted",
    st_waiting_budget: "Over budget",
    job: "Bounded run: {state}",
    approve: "Approve one model call for this task",
    launch: "Launch once",
    needApprove: "Approve this model call first.",
    cancelQueued: "Cancel queued task",
    cancelJob: "Cancel",
    recover: "Reconcile interruption",
    answer: "Show answer and quotations",
    hideAnswer: "Hide answer",
    draft: "Unverified cloud draft",
    quotes: "Quotations",
    claims: "Claims",
    cautions: "Cautions",
    prepareCloud: "Prepare cloud editing",
    cloudPrepared:
      "Cloud editing task prepared; it needs its own approval to run.",
    backups: "Backup model",
    loadBackups: "Load backup choices",
    noBackups: "This task's configuration declares no backup models.",
    backupOption: "{model} · task limit {limit}",
    approveBackup: "Explicitly approve this declared backup model",
    selectBackup: "Approve backup choice",
    needBackup: "Load the backup list and approve the choice explicitly.",
    audit: "Cost and audit",
    loadAudit: "Show call cost and audit",
    costState: "Cost state",
    reserved: "Reserved",
    charged: "Charged",
    unknown: "unknown",
    localCall: "Local call",
    tokens: "{i} in / {o} out tokens",
    selection: "Backup choice {n}",
    noCalls: "No model call recorded yet.",
    models: "Models & budget",
  },
});
const runTones = {
  succeeded: "success",
  failed: "error",
  interrupted: "error",
  cancelled: "info",
  waiting_budget: "error",
};
const stateText = (value) =>
  t("st_" + value) === "st_" + value ? value : t("st_" + value);

export function createView(ctx, route) {
  let tab = route.sub === "model" ? "model" : "search",
    epoch = 0,
    digest = null,
    clients = [];
  const tabHost = h("div");
  const body = h("div.a-stack");
  // Search
  const searchWho = select([], undefined, {
    dataset: { draft: "ignore" },
    "aria-label": t("identity"),
  });
  const query = input({
    type: "search",
    maxLength: 200,
    placeholder: t("placeholder"),
    value: route.params?.q || "",
    dataset: { draft: "ignore" },
    "aria-label": t("search"),
  });
  const results = h("div.a-stack", { "aria-live": "polite" });
  const go = h(
    "button.a-button.is-primary",
    { type: "submit" },
    icon("search"),
    t("go"),
  );
  const searchForm = h("form.a-ask-hero", query, searchWho, go);
  let vaultNames = new Map();
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void run(go, doSearch);
  });
  async function doSearch() {
    const text = query.value.trim();
    if (text.length < 2 || !searchWho.value) return;
    const mine = ++epoch;
    clear(results, skeleton(3));
    const result = await ctx.api.searchNotes(searchWho.value, text, 20);
    if (mine !== epoch) return;
    clear(
      results,
      h(
        "p.a-subtle",
        result.results.length
          ? t("hits", {
              n: result.results.length,
              v: result.searched_vault_ids.length,
            })
          : result.searched_vault_ids.length
            ? t("noHits")
            : t("noDefault"),
      ),
      result.results.map((hit) =>
        h(
          "article.a-hit",
          h(
            "header",
            h("span.a-hit-icon", icon("document")),
            h(
              "div",
              h("h3", hit.title || hit.note_path),
              h(
                "p.a-subtle",
                `${vaultNames.get(hit.vault_id) || hit.vault_id} · ${hit.note_path} · ${t("line", { n: hit.line })}`,
              ),
            ),
          ),
          h("p.a-hit-snippet", hit.snippet),
          h(
            "div.a-toolbar",
            button(t("askThis"), {
              variant: "small",
              iconName: "ask",
              onClick: () => prefillAsk(text, searchWho.value),
            }),
          ),
        ),
      ),
    );
  }
  // Ask
  const askWho = select([], undefined);
  const question = textarea({
    id: "atlas-ask-question",
    required: true,
    rows: 3,
    maxLength: 4000,
  });
  const retrieval = input({
    id: "atlas-ask-retrieval",
    required: true,
    maxLength: 400,
  });
  const submit = h(
    "button.a-button.is-primary",
    { type: "submit" },
    icon("plus"),
    t("submit"),
  );
  const askForm = h(
    "form.a-form",
    { dataset: { draft: "tracked" } },
    notice(t("askNote")),
    h(
      "div.a-form-grid",
      field(t("identity"), askWho),
      field(t("retrieval"), retrieval, { help: t("retrievalHelp") }),
    ),
    field(t("question"), question),
    h("div.a-form-actions", submit),
  );
  askForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void run(submit, async () => {
      if (!digest) throw new Error(t("needPolicy"));
      const acknowledge = captureDraftSave(askForm);
      await ctx.api.submitQuery({
        client_id: askWho.value,
        request_id: crypto.randomUUID(),
        configuration_sha256: digest,
        question: question.value,
        retrieval_query: retrieval.value,
        limit: 10,
        mode: "extractive",
      });
      acknowledge();
      toast(t("queued"), { tone: "success" });
      await loadTasks();
    });
  });
  const tasksHost = h("div.a-stack");
  function prefillAsk(text, client) {
    retrieval.value = text;
    if ([...askWho.options].some((o) => o.value === client))
      askWho.value = client;
    ctx.navigate("ask", "model");
    question.focus();
  }
  function taskCard(task, job) {
    const node = h("article.a-task");
    const extra = h("div.a-stack");
    const controls = h("div.a-form-actions");
    const act = (label, work, variant) => {
      const control = button(label, { variant: variant || "small" });
      control.addEventListener("click", () => run(control, work));
      controls.append(control);
      return control;
    };
    if (task.state === "queued" && !job) {
      const consent = checkbox(t("approve"));
      controls.append(consent.label);
      act(
        t("launch"),
        async () => {
          if (!consent.input.checked) throw new Error(t("needApprove"));
          await ctx.api.launchQueryJob({
            role: task.role,
            client_id: task.client_id,
            task_id: task.task_id,
            cloud_edit: false,
          });
          await loadTasks();
        },
        "primary",
      );
      act(t("cancelQueued"), async () => {
        await ctx.api.cancelQueryTask(task.role, task.task_id);
        await loadTasks();
      });
    }
    if (job && ["reserved", "running"].includes(job.state)) {
      act(t("cancelJob"), async () => {
        await ctx.api.cancelQueryJob(job.id);
        await loadTasks();
      });
      act(t("recover"), async () => {
        await ctx.api.recoverQueryJob(job.id);
        await loadTasks();
      });
    }
    if (task.state === "succeeded") {
      const answerBox = h("div.a-answer");
      const show = act(t("answer"), async () => {
        if (answerBox.isConnected) {
          answerBox.remove();
          show.textContent = t("answer");
          return;
        }
        const result = await ctx.api.queryResult(task.role, task.task_id);
        clear(
          answerBox,
          result.cloud_answer
            ? h(
                "div.a-stack",
                badge(t("draft"), "waiting"),
                h("pre.a-pre.is-reading", result.cloud_answer.text),
              )
            : null,
          result.claims?.length
            ? h(
                "div.a-stack",
                h("h4", t("claims")),
                h(
                  "ul.a-plain-list",
                  result.claims.map((c) => h("li", c.text)),
                ),
              )
            : null,
          result.quotations?.length
            ? h(
                "div.a-stack",
                h("h4", t("quotes")),
                result.quotations.map((q) => h("blockquote.a-quote", q.quote)),
              )
            : null,
          result.cautions?.length
            ? notice(
                h(
                  "ul.a-plain-list",
                  result.cautions.map((c) => h("li", c)),
                ),
                "warning",
              )
            : null,
        );
        extra.prepend(answerBox);
        show.textContent = t("hideAnswer");
      });
      if (task.role === "local")
        act(t("prepareCloud"), async () => {
          await ctx.api.submitCloudQuery({
            client_id: task.client_id,
            local_task_id: task.task_id,
            request_id: crypto.randomUUID(),
            configuration_sha256: task.configuration_sha256,
          });
          toast(t("cloudPrepared"), { tone: "success" });
          await loadTasks();
        });
    }
    if (task.role === "cloud" && task.state === "queued" && !job) {
      const choice = select([]);
      const consent = checkbox(t("approveBackup"));
      const load = button(t("loadBackups"), { variant: "small" });
      load.addEventListener("click", () =>
        run(load, async () => {
          const detail = await ctx.api.queryDetail("cloud", task.task_id);
          setOptions(
            choice,
            detail.backup_choices.map((c) => [
              String(c.index),
              t("backupOption", {
                model: c.model,
                limit: usd(c.task_limit_nusd),
              }),
            ]),
          );
          if (!detail.backup_choices.length)
            toast(t("noBackups"), { tone: "info" });
        }),
      );
      const approve = button(t("selectBackup"), { variant: "small" });
      approve.addEventListener("click", () =>
        run(approve, async () => {
          if (!consent.input.checked || !choice.value)
            throw new Error(t("needBackup"));
          await ctx.api.selectQueryBackup(task.task_id, {
            client_id: task.client_id,
            backup_index: Number(choice.value),
            selection_id: crypto.randomUUID(),
            approve: true,
          });
          await loadTasks();
        }),
      );
      extra.append(
        disclosure(
          t("backups"),
          h("div.a-toolbar", load),
          field(t("backups"), choice),
          consent.label,
          h("div", approve),
        ),
      );
    }
    const auditBox = h("div.a-stack");
    const auditButton = button(t("loadAudit"), {
      variant: "small",
      iconName: "coin",
    });
    auditButton.addEventListener("click", () =>
      run(auditButton, async () => {
        const detail = await ctx.api.queryDetail(task.role, task.task_id);
        const pairs = [];
        if (detail.cloud_call) {
          const call = detail.cloud_call;
          pairs.push(
            [t("costState"), call.state],
            [t("reserved"), usd(call.reserved_nusd)],
            [
              t("charged"),
              call.charged_nusd === null || call.charged_nusd === undefined
                ? t("unknown")
                : usd(call.charged_nusd),
            ],
          );
        }
        if (detail.local_call)
          pairs.push([
            t("localCall"),
            `${detail.local_call.state} · ${t("tokens", { i: detail.local_call.input_tokens ?? t("unknown"), o: detail.local_call.output_tokens ?? t("unknown") })}`,
          ]);
        for (const s of detail.selections || [])
          pairs.push([
            t("selection", { n: s.backup_index }),
            `${s.state} ${s.reason || ""}`,
          ]);
        clear(
          auditBox,
          pairs.length ? facts(pairs) : h("p.a-subtle", t("noCalls")),
        );
      }),
    );
    extra.append(disclosure(t("audit"), auditButton, auditBox));
    node.append(
      h(
        "header.a-task-head",
        h("span.a-row-icon", icon(task.role === "local" ? "ask" : "spark")),
        h(
          "div.a-card-titles",
          h(
            "h3",
            task.question ||
              `${task.client_name} · ${task.role === "local" ? t("local") : t("cloud")}`,
          ),
          h(
            "p.a-subtle",
            [
              task.client_name,
              task.role === "local" ? t("local") : t("cloud"),
              shortId(task.task_id),
            ].join(" · "),
          ),
        ),
        badge(stateText(task.state), runTones[task.state] || "waiting"),
      ),
      job
        ? h(
            "p.a-subtle",
            t("job", { state: stateText(job.state) }) +
              (job.error_code ? " · " + job.error_code : ""),
          )
        : null,
      task.error_code ? h("p.a-inline-error", task.error_code) : null,
      controls.childElementCount ? controls : null,
      extra,
    );
    return node;
  }
  async function loadTasks() {
    const mine = epoch;
    const data = await ctx.api.queryTasks();
    if (mine !== epoch) return;
    clear(
      tasksHost,
      data.tasks.length
        ? data.tasks.map((task) =>
            taskCard(
              task,
              data.jobs.find(
                (j) => j.role === task.role && j.task_id === task.task_id,
              ),
            ),
          )
        : empty(t("none"), { iconName: "ask" }),
    );
  }
  function drawTabs() {
    clear(
      tabHost,
      tabs(
        [
          { value: "search", label: t("search") },
          { value: "model", label: t("ask") },
        ],
        tab,
        (value) =>
          ctx.navigate(
            "ask",
            value === "model" ? "model" : "",
            value === "search" && query.value ? { q: query.value } : {},
          ),
        t("search"),
      ),
    );
  }
  async function refresh() {
    const mine = ++epoch;
    drawTabs();
    let components, vaults, policy;
    try {
      [clients, vaults, components, policy] = await Promise.all([
        ctx.store.clients({ force: true }),
        ctx.store.vaults(),
        ctx.store.components(),
        ctx.api.queryPolicy().catch(() => null),
      ]);
    } catch (error) {
      clear(body, failure(error, refresh));
      return;
    }
    if (mine !== epoch) return;
    digest = policy?.configured ? policy.configuration_sha256 : null;
    vaultNames = new Map(vaults.map((v) => [v.id, v.name]));
    const searchers = clients.filter(
      (c) => c.active && c.grants.some((g) => g.can_query),
    );
    setOptions(
      searchWho,
      searchers.map((c) => [c.id, c.name]),
      searchers.some((c) => c.id === searchWho.value)
        ? searchWho.value
        : undefined,
    );
    const askers = clients.filter((c) => c.active);
    setOptions(
      askWho,
      askers.map((c) => [c.id, c.name]),
      askers.some((c) => c.id === askWho.value) ? askWho.value : undefined,
    );
    if (tab === "search") {
      clear(
        body,
        card(
          { className: "a-ask-card" },
          components.gui_features?.note_search === false
            ? notice(t("noFeature"), "warning")
            : searchers.length
              ? searchForm
              : notice(t("noIdentity"), "warning"),
          h("p.a-subtle", t("searchNote")),
        ),
        results,
      );
      if (query.value.trim().length >= 2 && searchers.length)
        await run(go, doSearch);
      query.focus();
    } else {
      clear(
        body,
        card(
          { title: t("ask") },
          digest
            ? null
            : notice(
                t("needPolicy"),
                "warning",
                button(t("models"), {
                  variant: "small",
                  onClick: () => ctx.navigate("models"),
                }),
              ),
          askForm,
        ),
        card({ title: t("tasks") }, tasksHost),
      );
      rememberDraft(askForm);
      clear(tasksHost, skeleton(3));
      await loadTasks();
    }
  }
  return {
    node: h("div.a-stack", tabHost, body),
    refresh,
    update(next) {
      const nextTab = next.sub === "model" ? "model" : "search";
      if (next.params?.q) query.value = next.params.q;
      if (nextTab !== tab) {
        tab = nextTab;
        void refresh();
      } else if (tab === "search" && next.params?.q) void run(go, doSearch);
      return true;
    },
    dispose: () => epoch++,
  };
}
