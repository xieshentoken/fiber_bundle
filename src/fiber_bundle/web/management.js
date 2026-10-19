import { phrase, uiText } from "./i18n/index.js";
// Bounded inventory views use the authenticated API.
import { $, action, el, captureMessage } from "./ui.js";
import { createSourcePolicyView } from "./source_policy.js";
import { control, showJSON } from "./record_ui.js";
import { receiptSummary } from "./inventory_summary.js";
import { helpTip } from "./help_tip.js";
export function createInventoryView(api) {
  const policy = createSourcePolicyView(api, refresh);
  let epoch = 0,
    offset = 0;
  const root = $("inventory-content"),
    client = $("inventory-client"),
    vault = $("inventory-vault");
  async function refresh() {
    const revision = ++epoch;
    const [receipts, clients, vaults] = await Promise.all([
      api.inventoryReceipts(),
      api.clients(),
      api.vaults(),
    ]);
    if (revision !== epoch) return;
    captureMessage("inventory", "inventory-versions")("");
    for (const [select, items] of [
      [client, clients.filter((c) => c.active)],
      [vault, vaults.filter((v) => v.status === "active")],
    ]) {
      const previous = select.value;
      select.replaceChildren();
      for (const item of items) {
        const option = el("option", item.name);
        option.value = item.id;
        select.append(option);
      }
      if ([...select.options].some((o) => o.value === previous))
        select.value = previous;
    }
    const heading = el("h3", uiText("最近收件"));
    heading.append(
      helpTip(
        "收件与笔记保存的区别",
        "这里显示原件收件记录。正文保存和索引是否完成，请在收件箱查看实际结果。",
      ),
    );
    root.replaceChildren(heading);
    if (!receipts.length) root.append(el("p", uiText("暂无收件记录。")));
    for (const receipt of receipts) {
      const row = el("article", null, "inventory-summary"),
        summary = receiptSummary(receipt);
      row.append(
        el(
          "h3",
          receipt.title || receipt.filename
            ? summary.title
            : uiText(summary.title),
        ),
        el("span", uiText(summary.state), "status-badge " + summary.tone),
        el("p", uiText(summary.detail), "muted"),
      );
      if (receipt.error_code) row.append(el("p", receipt.error_code, "error"));
      showJSON(row, receipt, "收件详情与编号");
      root.append(row);
    }
    await policy.refresh();
  }
  async function versions() {
    const revision = ++epoch,
      fixedClient = client.value,
      fixedVault = vault.value,
      fixedOffset = offset,
      notify = captureMessage("inventory", "inventory-versions");
    root.replaceChildren(
      el("p", uiText("正在读取所选身份和知识库的来源版本…")),
    );
    let rows;
    try {
      rows = await api.inventoryVersions(fixedClient, fixedVault, fixedOffset);
    } catch (error) {
      if (revision !== epoch) return;
      const text =
        error.status === 404
          ? "当前身份无法访问该知识库或来源。请核对客户端授权；刷新不会授予权限。"
          : error.message;
      root.replaceChildren(el("p", uiText(text), "error"));
      notify(text, true);
      return;
    }
    if (revision !== epoch) return;
    notify("");
    root.replaceChildren(
      el("h3", phrase("来源版本 · 第 {p0} 页", { p0: fixedOffset / 20 + 1 })),
    );
    if (!rows.length) root.append(el("p", uiText("此页无可访问版本。")));
    for (const version of rows) {
      const card = el("article", null, "row");
      card.append(
        el(
          "h3",
          uiText(
            version.assets?.[0]?.download_name ||
              version.assets?.[0]?.original_name ||
              "来源资料",
          ),
        ),
        el(
          "p",
          phrase("版本 {p0} · {p1}", {
            p0: version.version_no,
            p1: version.retention,
          }),
        ),
      );
      showJSON(
        card,
        { id: version.id, source_key: version.source_key },
        "来源身份详情",
      );
      for (const asset of version.assets) {
        card.append(
          control(
            "取回原件：" + (asset.download_name || asset.original_name),
            () =>
              api.downloadOriginal(
                fixedClient,
                fixedVault,
                version.id,
                asset.id,
              ),
          ),
        );
      }
      if (
        version.archive_projection?.references?.some(
          (r) => r.state !== "resolved",
        )
      )
        card.append(
          el(
            "p",
            uiText("存在无法唯一关联的图片引用，需核对 ZIP 原件。"),
            "error",
          ),
        );
      root.append(card);
    }
    root.append(
      control("上一页", async () => {
        offset = Math.max(0, offset - 20);
        await versions();
      }),
      control("下一页", async () => {
        offset += 20;
        await versions();
      }),
    );
  }
  action($("inventory-refresh"), refresh);
  for (const select of [client, vault])
    select.addEventListener("change", () => {
      epoch++;
      policy.clear();
      offset = 0;
      captureMessage("inventory", "inventory-versions")("");
      root.replaceChildren(
        el("p", uiText("选择已变化，请查看当前范围的来源版本。")),
      );
    });
  action($("inventory-versions"), async () => {
    offset = 0;
    await versions();
  });
  return {
    refresh,
    clear() {
      epoch++;
      policy.clear();
      offset = 0;
      root.replaceChildren();
      client.replaceChildren();
      vault.replaceChildren();
    },
  };
}
