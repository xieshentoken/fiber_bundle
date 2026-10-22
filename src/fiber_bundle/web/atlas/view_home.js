// Home: a read-only overview. Nothing here starts models, channels, indexing or publishing.
import { summaryCost, ledgerCost, knownCost } from "./usage_model.js";
import { createArchitecture } from "./home_architecture.js";
import { h, clear } from "./dom.js";
import { strings, lang } from "./i18n.js";
import { costSummary } from "../home_model.js";
import { pendingItems } from "../home_status.js";
import { createHomeLoader } from "./home_loader.js";
import {
  agentRows,
  criticalAlert,
  recentImports,
  todoRows,
  vaultRows,
  readText,
} from "./home_model.js";
import {
  button,
  card,
  empty,
  listRow,
  notice,
  select,
  setOptions,
  skeleton,
  stat,
} from "./ui.js";
import { chartWithTable } from "./chart.js";
import { relative, usd, number } from "./format.js";

const t = strings({
  zh: {
    morning: "早上好",
    noon: "中午好",
    afternoon: "下午好",
    evening: "晚上好",
    summary:
      "{date} · 下面每项状态都来自最近一次读取；未读取的不会显示为正常或 0。",
    ask: "提问与检索",
    import: "导入资料",
    waiting: "待你处理",
    waitingHint: "来自最近读取的列表",
    vaults: "已接入知识库",
    agents: "已授权 Agent",
    cost: "近 30 天费用",
    costHint: "按配置单价估算",
    change: "较前 30 天 {p}",
    attention: "需要你处理",
    openInbox: "打开收件箱",
    allClear: "已读取范围内没有待处理事项。",
    health: "系统状况",
    healthNote: "最近读取的状态，不是实时流量；模型未主动探测。",
    vaultCard: "知识库",
    manage: "管理",
    noVaults: "尚未接入知识库。",
    connect: "接入笔记仓库",
    defaultQuery: "默认查询",
    snapshot: "快照 {time}",
    stale: "快照待更新",
    importsCard: "最近导入",
    identity: "身份",
    vault: "知识库",
    noScope: "没有可新增原件的身份与库；先在 Agent 与客户端授予权限。",
    noImports: "所选范围内暂无导入记录。",
    ordinary: "普通导入",
    expert: "fake-expert",
    agentCard: "外部 Agent",
    noAgents: "尚未配置 Agent 连接。",
    addAgent: "添加连接",
    costCard: "每日估算费用",
    costNote: "{start} – {end}（UTC）· {calls} 次记录调用 · 预留 {reserved}",
    localCalls: "本地调用 {n} 次（不计费）",
    costUnread: "费用尚未读取",
    unread: "尚未读取",
    calls: "调用",
    openBackend: "查看后台与提醒",
  },
  en: {
    morning: "Good morning",
    noon: "Good day",
    afternoon: "Good afternoon",
    evening: "Good evening",
    summary:
      "{date} · Every state below comes from the latest read; anything unread is never shown as healthy or zero.",
    ask: "Ask & search",
    import: "Import",
    waiting: "Waiting for you",
    waitingHint: "From the latest reads",
    vaults: "Connected vaults",
    agents: "Authorized agents",
    cost: "Cost, last 30 days",
    costHint: "Estimated from configured prices",
    change: "{p} vs previous 30 days",
    attention: "Needs your attention",
    openInbox: "Open inbox",
    allClear: "Nothing waiting within the scope that was read.",
    health: "System status",
    healthNote:
      "States from the latest reads, not live traffic. Models are not probed.",
    vaultCard: "Vaults",
    manage: "Manage",
    noVaults: "No vault connected yet.",
    connect: "Connect a vault",
    defaultQuery: "Default query",
    snapshot: "Snapshot {time}",
    stale: "Snapshot needs update",
    importsCard: "Recent imports",
    identity: "Identity",
    vault: "Vault",
    noScope:
      "No identity can add originals to a vault yet. Grant access in Agents & clients first.",
    noImports: "No imports in this scope yet.",
    ordinary: "Regular import",
    expert: "fake-expert",
    agentCard: "External agents",
    noAgents: "No agent connections yet.",
    addAgent: "Add connection",
    costCard: "Estimated daily cost",
    costNote:
      "{start} – {end} (UTC) · {calls} recorded calls · {reserved} reserved",
    localCalls: "{n} local calls (not billed)",
    costUnread: "Cost not read yet",
    unread: "Not read yet",
    calls: "Calls",
    openBackend: "Open Backend",
  },
});
function greeting(date = new Date()) {
  const hour = date.getHours();
  return t(
    hour >= 5 && hour < 11
      ? "morning"
      : hour < 13 && hour >= 11
        ? "noon"
        : hour >= 13 && hour < 18
          ? "afternoon"
          : "evening",
  );
}
function today() {
  return new Intl.DateTimeFormat(lang() === "zh" ? "zh-CN" : "en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(new Date());
}

export function createView(ctx) {
  const root = h("div.a-home");
  const dashboard = createArchitecture(ctx);
  let frame = 0;
  const loader = createHomeLoader(ctx.api, ctx.session, () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(draw);
  });
  const go = (page, sub, params) => () => ctx.navigate(page, sub, params);

  function draw() {
    const records = loader.records;
    dashboard.update(records);
    const todo = todoRows(records);
    ctx.setBadge("worklist", pendingItems(records).length);
    const alert = criticalAlert(records);
    const cost =
      records.usage && !records.usage.error
        ? costSummary(records.usage.value)
        : null;
    const vaults = vaultRows(records);
    const agents = agentRows(records);
    clear(
      root,
      alert
        ? notice(
            h("strong", alert.title),
            "error",
            h("p", alert.body),
            button(t("openBackend"), {
              variant: "small",
              onClick: go(alert.page),
            }),
          )
        : null,
      h(
        "div.a-stats",
        stat(
          t("waiting"),
          Object.keys(records).length ? number(todo.length) : "—",
          {
            hint: t("waitingHint"),
            tone: todo.length ? "waiting" : undefined,
            onClick: go("worklist"),
          },
        ),
        stat(
          t("vaults"),
          records.vaults?.value
            ? number(vaults.filter((v) => v.status[1] === "success").length)
            : "—",
          {
            hint: records.vaults ? readText(records.vaults) : t("unread"),
            onClick: go("vaults"),
          },
        ),
        stat(
          t("agents"),
          records.agents?.value
            ? number(agents.filter((a) => a.status[1] === "success").length)
            : "—",
          {
            hint: records.agents ? readText(records.agents) : t("unread"),
            onClick: go("clients"),
          },
        ),
        stat(t("cost"), cost ? summaryCost(cost) : "—", {
          hint:
            cost && cost.change !== null
              ? t("change", {
                  p:
                    (cost.change > 0 ? "+" : "") +
                    Math.round(cost.change * 100) +
                    "%",
                })
              : t("costHint"),
          onClick: go("execution"),
        }),
      ),
      dashboard.node,
      attentionCard(todo, records),
      h(
        "div.a-grid-3",
        vaultCard(vaults, records),
        importsCard(records),
        agentCard(agents, records),
      ),
      costCard(cost, records),
    );
  }
  function attentionCard(rows, records) {
    const list = h("div.a-rows");
    for (const row of rows.slice(0, 7))
      list.append(
        listRow({
          iconName: {
            batch: "batch",
            documents: "document",
            media: "media",
            articles: "article",
            vault: "approval",
            imports: "imports",
            expert: "expert",
            automatic: "automatic",
            channel: "channels",
          }[row.source],
          title: row.title,
          detail: [row.sourceLabel, row.detail].filter(Boolean).join(" · "),
          status: row.status,
          onClick: () => openItem(row),
        }),
      );
    return card(
      {
        title: t("attention"),
        className: "is-flush",
        actions: rows.length
          ? button(t("openInbox"), {
              variant: "ghost",
              iconName: "chevron",
              onClick: go("worklist"),
            })
          : null,
      },
      !Object.keys(records).length
        ? skeleton(4)
        : rows.length
          ? list
          : empty(t("allClear"), { iconName: "check" }),
    );
  }
  function openItem(row) {
    if (row.source === "automatic")
      return ctx.navigate("vaults", "detail", {
        vault: row.vault_id,
        tab: "automation",
      });
    if (row.source === "videos")
      return ctx.navigate("inventory", "", {
        tab: "video",
        client: row.client_id,
        vault: row.vault_id,
      });
    const sections = {
      vault: "pending",
      batch: "batches",
      documents: "documents",
      articles: "documents",
      media: "media",
    };
    if (sections[row.source])
      ctx.navigate("worklist", sections[row.source], { id: row.id });
    else if (row.page === "imports")
      ctx.navigate("imports", "", {
        client: row.client_id,
        vault: row.vault_id,
      });
    else ctx.navigate(row.page);
  }
  function vaultCard(rows, records) {
    const list = h("div.a-rows");
    for (const row of rows.slice(0, 6))
      list.append(
        listRow({
          iconName: "vaults",
          title: row.name,
          detail: [
            row.notes,
            row.defaultQuery ? t("defaultQuery") : null,
            row.builtAt
              ? row.stale
                ? t("stale")
                : t("snapshot", { time: relative(row.builtAt) })
              : null,
          ]
            .filter(Boolean)
            .join(" · "),
          status: row.status,
          onClick: go("vaults", "", { vault: row.id }),
        }),
      );
    return card(
      {
        title: t("vaultCard"),
        className: "is-flush",
        actions: button(t("manage"), {
          variant: "ghost",
          onClick: go("vaults"),
        }),
      },
      !records.vaults
        ? skeleton(3)
        : records.vaults.error && !rows.length
          ? empty(readText(records.vaults), { iconName: "alert" })
          : rows.length
            ? list
            : empty(t("noVaults"), {
                iconName: "vaults",
                action: button(t("connect"), {
                  variant: "primary",
                  onClick: go("vaults", "connect"),
                }),
              }),
    );
  }
  function importsCard(records) {
    const choices = loader.scopeChoices();
    const scope = loader.scope;
    const identity = select(
      choices.clients.map((c) => [c.id, c.name]),
      scope.client_id,
      { "aria-label": t("identity"), dataset: { draft: "ignore" } },
    );
    const vault = select(
      choices.vaults.map((v) => [v.id, v.name]),
      scope.vault_id,
      { "aria-label": t("vault"), dataset: { draft: "ignore" } },
    );
    identity.addEventListener("change", () => {
      void loader.setScope({ client_id: identity.value });
      setOptions(
        vault,
        loader.scopeChoices().vaults.map((v) => [v.id, v.name]),
        loader.scope.vault_id,
      );
    });
    vault.addEventListener(
      "change",
      () =>
        void loader.setScope({
          client_id: identity.value,
          vault_id: vault.value,
        }),
    );
    const rows = recentImports(records);
    const list = h("div.a-rows");
    for (const row of rows.slice(0, 6))
      list.append(
        listRow({
          iconName: row.iconName,
          title: row.title,
          detail: row.detail,
          status: row.status,
          onClick: go(row.page, row.sub || "", row.params || {}),
        }),
      );
    return card(
      {
        title: t("importsCard"),
        className: "is-flush",
        actions: h(
          "div.a-card-actions",
          button(t("ordinary"), {
            variant: "small",
            iconName: "imports",
            onClick: go("imports", "", {
              engine: "ordinary",
              ...(scope.client_id
                ? { client: scope.client_id, vault: scope.vault_id }
                : {}),
            }),
          }),
          button(t("expert"), {
            variant: "small",
            iconName: "expert",
            onClick: go("imports", "", {
              engine: "fake-expert",
              ...(scope.client_id
                ? { client: scope.client_id, vault: scope.vault_id }
                : {}),
            }),
          }),
        ),
      },
      choices.clients.length
        ? h("div.a-scope-bar", identity, vault)
        : records.clients
          ? notice(t("noScope"), "info")
          : null,
      !records.batches
        ? skeleton(3)
        : rows.length
          ? list
          : empty(t("noImports"), { iconName: "imports" }),
    );
  }
  function agentCard(rows, records) {
    const list = h("div.a-rows");
    for (const row of rows.slice(0, 6))
      list.append(
        listRow({
          iconName: "clients",
          title: row.name,
          detail: row.detail,
          status: row.status,
          onClick: go("clients", "agents", { id: row.id }),
        }),
      );
    return card(
      {
        title: t("agentCard"),
        className: "is-flush",
        actions: button(t("manage"), {
          variant: "ghost",
          onClick: go("clients"),
        }),
      },
      !records.agents
        ? skeleton(3)
        : records.agents.error
          ? empty(readText(records.agents), { iconName: "alert" })
          : rows.length
            ? list
            : empty(t("noAgents"), {
                iconName: "clients",
                action: button(t("addAgent"), {
                  onClick: go("clients", "agents"),
                }),
              }),
    );
  }
  function costCard(cost, records) {
    if (!cost)
      return card(
        { title: t("costCard") },
        records.usage?.error
          ? empty(readText(records.usage), { iconName: "alert" })
          : empty(t("costUnread"), { iconName: "coin" }),
      );
    return card(
      {
        title: t("costCard"),
        subtitle: t("costNote", {
          start: cost.start,
          end: cost.end,
          calls: number(cost.calls),
          reserved: usd(cost.reserved),
        }),
        actions:
          cost.localCalls !== null
            ? h(
                "span.a-subtle",
                t("localCalls", { n: number(cost.localCalls) }),
              )
            : null,
      },
      chartWithTable({
        title: t("costCard"),
        rows: cost.days.map((day) => ({
          day: day.day,
          ...day,
          value: knownCost(day),
          calls: day.calls,
        })),
        format: (value, row) =>
          row ? ledgerCost(row) : usd(Math.round(value)),
        scale: 1e9,
        extra: (row) => `${t("calls")} ${row.calls}`,
        columns: [
          {
            label: t("calls"),
            value: (row) => number(row.calls),
            className: "is-num",
          },
        ],
      }),
    );
  }
  draw();
  loader.start();
  return {
    node: root,
    header: {
      title: greeting(),
      summary: t("summary", { date: today() }),
      actions: [
        button(t("import"), { iconName: "imports", onClick: go("imports") }),
        button(t("ask"), {
          variant: "primary",
          iconName: "ask",
          onClick: go("ask"),
        }),
      ],
    },
    refresh: () => loader.refresh(true),
    dispose() {
      cancelAnimationFrame(frame);
      dashboard.dispose();
      loader.dispose();
    },
  };
}
