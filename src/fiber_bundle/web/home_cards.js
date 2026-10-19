import { joinText, textNode, setText, uiText, phrase } from "./i18n/index.js";
// Home cards: presentation of home_model rows. Navigation uses the shared data-page-target.
import { el } from "./ui.js";
import { readState, shortTime } from "./home_status.js";
import { todoGroups } from "./home_model.js";

export const readLabels = {
  channels: "消息入口",
  backend: "后台",
  executions: "执行",
  vaults: "知识库",
  clients: "客户端",
  batches: "批次",
  documents: "文档",
  media: "媒体",
  videos: "视频处理",
  articles: "文章",
  imports: "导入",
  expert: "专家指令",
  automatic: "自动处理",
  query: "模型路由",
  source: "提取配置",
  components: "组件合同",
  usage: "费用",
  agents: "Agent",
  backups: "备份",
  index: "索引快照",
};
export { jump } from "./ui_cards.js";
import {
  card,
  jump,
  row,
  stampLine,
  stampText,
  pill,
  link,
} from "./ui_cards.js";

let filter = null;
export function todoCard(rows, records, scopeText, rerender) {
  const { node } = card("需要你处理", "home-todo-title", "收件箱", "worklist");
  node.classList.add("todo-card");
  const groups = todoGroups(rows);
  if (filter && !groups.some((g) => g.source === filter)) filter = null;
  if (groups.length) {
    const chips = el("div", null, "ui-chips");
    chips.setAttribute("role", "group");
    setText(chips, uiText("按来源筛选"), "aria-label");
    for (const group of groups) {
      const chip = el("button", null, "ui-chip");
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(filter === group.source));
      chip.append(
        textNode(joinText([uiText(group.label), " "])),
        el("b", String(group.count)),
      );
      chip.addEventListener("click", () => {
        filter = filter === group.source ? null : group.source;
        rerender();
      });
      chips.append(chip);
    }
    node.append(chips);
  }
  const shown = rows.filter((r) => !filter || r.source === filter);
  const list = el("div", null, "ui-rows");
  for (const item of shown.slice(0, 5)) list.append(row(item, item.detail));
  if (!rows.length)
    list.append(
      el(
        "p",
        uiText(
          "已读取范围内暂无明确待处理事项；尚未读取或失败的来源见“读取范围与缺口”。",
        ),
        "ui-empty",
      ),
    );
  node.append(list);
  if (shown.length > 5)
    node.append(
      jump(
        `还有 ${shown.length - 5} 项 · 打开收件箱`,
        "worklist",
        "quiet ui-more",
      ),
    );
  const details = el("details", null, "ui-details");
  details.append(
    el("summary", uiText("读取范围与缺口")),
    el(
      "p",
      uiText(
        "文档、媒体、文章及所选范围导入：最近50条；批次、执行及专家指令：最多100条。跨来源可能重复，不作为全库总量。",
      ),
      "muted",
    ),
  );
  for (const name of [
    "vaults",
    "batches",
    "documents",
    "media",
    "videos",
    "articles",
    "imports",
    "expert",
    "automatic",
  ]) {
    const scoped = ["imports", "expert", "automatic", "videos"].includes(name);
    details.append(
      el(
        "p",
        joinText(
          [
            uiText(readLabels[name]),
            scoped
              ? scopeText || uiText("尚未选择身份 / 库")
              : uiText("管理员可见范围"),
            uiText(stampText(records[name])),
          ],
          " · ",
        ),
        records[name]?.error ? "error" : "muted",
      ),
    );
  }
  node.append(details);
  return node;
}

export function importsCard(rows, records, scopeNode, owner) {
  const { node, head } = card("最近导入", "home-imports-title");
  node.classList.add("imports-card");
  const actions = el("div", null, "ui-card-actions");
  for (const [text, engine, primary] of [
    ["普通资料导入", "ordinary", true],
    ["fake-expert 导入", "fake-expert", false],
  ]) {
    const button = jump(
      text,
      "imports",
      primary ? "primary ui-small" : "ui-small",
    );
    button.dataset.importEngine = engine;
    if (owner.client_id && owner.vault_id)
      Object.assign(button.dataset, {
        clientId: owner.client_id,
        vaultId: owner.vault_id,
      });
    actions.append(button);
  }
  head.append(actions);
  node.append(scopeNode);
  const list = el("div", null, "ui-rows");
  for (const item of rows.slice(0, 3))
    list.append(
      row(
        item,
        [item.detail, item.time ? shortTime(item.time) : null]
          .filter(Boolean)
          .join(" · "),
      ),
    );
  if (!rows.length)
    list.append(
      el(
        "p",
        records.imports?.error
          ? phrase("导入记录：{p0}", {
              p0: uiText(readState(records.imports).text),
            })
          : records.imports
            ? uiText("所选范围暂无导入或收件批次。")
            : uiText("所选范围尚未读取。"),
        "ui-empty",
      ),
    );
  node.append(list);
  const foot = el("div", null, "ui-card-foot");
  if (rows.length > 3)
    foot.append(
      jump(
        `还有 ${rows.length - 3} 条 · 查看全部导入`,
        "imports",
        "quiet ui-more",
      ),
    );
  foot.append(link("资料与版本", "inventory"));
  node.append(foot);
  if (records.imports?.error) node.append(stampLine(records.imports));
  return node;
}

export function vaultsCard(rows, records) {
  const { node } = card("知识库", "home-vaults-title", "管理", "vaults");
  const list = el("div", null, "ui-facts");
  for (const vault of rows) {
    const item = el("div", null, "ui-fact");
    const meta = [
      vault.notes,
      vault.builtAt ? "快照 " + shortTime(vault.builtAt) : null,
    ]
      .filter(Boolean)
      .join(" · ");
    item.append(
      el("strong", vault.name),
      el("small", uiText(meta || "笔记数未记录")),
    );
    item.append(
      pill(
        vault.stale ? "快照待更新" : vault.status,
        vault.stale ? "waiting" : vault.tone,
      ),
    );
    list.append(item);
  }
  if (!rows.length)
    list.append(
      el(
        "p",
        records.vaults?.error
          ? uiText(readState(records.vaults).text)
          : records.vaults
            ? uiText("尚无知识库。")
            : uiText("尚未读取知识库。"),
        "ui-empty",
      ),
    );
  node.append(
    list,
    el(
      "p",
      records.index
        ? uiText("快照信息仅供定位；当前文件变化未核对。")
        : uiText("轻量摘要尚未接入；显式索引核对留在原页面。"),
      "ui-note",
    ),
    stampLine(records.index || records.vaults),
  );
  return node;
}

export function agentsCard(rows, records) {
  const { node } = card("外部 Agent", "home-agents-title", "客户端", "clients");
  const list = el("div", null, "ui-facts");
  for (const agent of rows.slice(0, 4)) {
    const item = el("div", null, "ui-fact");
    item.append(
      el("strong", agent.name),
      el(
        "small",
        joinText(
          agent.detail.split(" · ").map((part) => uiText(part)),
          " · ",
        ),
      ),
      pill(agent.status, agent.tone),
    );
    list.append(item);
  }
  if (!rows.length)
    list.append(
      el(
        "p",
        records.agents?.error
          ? uiText(readState(records.agents).text)
          : records.agents
            ? uiText("尚无已登记连接。")
            : uiText("尚未读取连接。"),
        "ui-empty",
      ),
    );
  node.append(
    list,
    el("p", uiText("连接状态来自配置记录；首页未自动探测或问答。"), "ui-note"),
    stampLine(records.agents),
  );
  return node;
}
