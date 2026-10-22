// Vault approvals: a client asked for a new empty vault. Approval names the client and
// the exact grants, and is confirmed explicitly. Nothing can be written before approval.
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  card,
  checkbox,
  empty,
  field,
  notice,
  select,
  skeleton,
  failure,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";

const t = strings({
  zh: {
    intro: "核对申请方、目标库与授予权限后再批准启用。审批前不能写入资料。",
    none: "没有待审批的空库。",
    requester: "申请方：{name}",
    unavailable: "已不可用",
    client: "授权给",
    grants: "授予权限",
    query: "查询",
    original: "取原件",
    add: "新增",
    approve: "批准并启用",
    noClient: "请先创建有效客户端。",
    confirmTitle: "批准空库“{name}”？",
    confirmBody: "将为下列客户端启用此库并授予所选权限：",
    approved: "已批准并启用。",
  },
  en: {
    intro:
      "Check the requester, the vault and the grants before approving. Nothing can be written before approval.",
    none: "No vault requests waiting for approval.",
    requester: "Requested by {name}",
    unavailable: "no longer available",
    client: "Grant to",
    grants: "Grants",
    query: "Query",
    original: "Originals",
    add: "Add",
    approve: "Approve and enable",
    noClient: "Create an active client first.",
    confirmTitle: "Approve vault “{name}”?",
    confirmBody:
      "The vault will be enabled for this client with the selected grants:",
    approved: "Approved and enabled.",
  },
});

export function create(ctx, params, onChanged) {
  const root = h("div.a-stack");
  let generation = 0;
  async function refresh() {
    const mine = ++generation;
    clear(root, skeleton(3));
    let vaults, clients;
    try {
      [vaults, clients] = await Promise.all([
        ctx.store.vaults({ force: true }),
        ctx.store.clients({ force: true }),
      ]);
    } catch (error) {
      if (mine === generation) clear(root, failure(error, refresh));
      return;
    }
    if (mine !== generation) return;
    const pending = vaults.filter((vault) => vault.status === "pending");
    clear(
      root,
      notice(t("intro")),
      pending.length
        ? pending.map((vault) => approvalCard(vault, clients))
        : empty(t("none"), { iconName: "approval" }),
    );
    if (params.id)
      root
        .querySelector(`[data-id="${CSS.escape(params.id)}"]`)
        ?.scrollIntoView({ block: "center" });
  }
  function approvalCard(vault, clients) {
    const requester = clients.find(
      (client) => client.id === vault.requested_by_client_id,
    );
    const active = clients.filter((client) => client.active);
    const who = select(
      active.map((client) => [client.id, client.name]),
      vault.requested_by_client_id,
    );
    const boxes = ["query", "original", "add"].map((name) => ({
      name,
      ...checkbox(t(name), false),
    }));
    const form = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      h(
        "div.a-form-grid",
        field(t("client"), who),
        h(
          "div.a-field",
          h("label", t("grants")),
          h(
            "div.a-toolbar",
            boxes.map((box) => box.label),
          ),
        ),
      ),
      h(
        "div.a-form-actions",
        h("button.a-button.is-primary", { type: "submit" }, t("approve")),
      ),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = form.querySelector("[type=submit]");
      void run(button, async () => {
        if (!who.value) throw new Error(t("noClient"));
        const grants = Object.fromEntries(
          boxes.map((box) => [box.name, box.input.checked]),
        );
        const ok = await confirm({
          title: t("confirmTitle", { name: vault.name }),
          body: [
            t("confirmBody"),
            {
              items: [
                who.selectedOptions[0]?.textContent || who.value,
                ...boxes.filter((b) => b.input.checked).map((b) => t(b.name)),
              ],
            },
          ],
          confirmLabel: t("approve"),
        });
        if (!ok) return false;
        await ctx.api.approveVault(vault.id, who.value, grants);
        ctx.store.invalidate("vaults", "clients");
        toast(t("approved"), { tone: "success" });
        await Promise.all([refresh(), onChanged()]);
      });
    });
    return h(
      "div",
      { dataset: { id: vault.id } },
      card(
        {
          title: vault.name,
          subtitle: t("requester", {
            name: requester?.name || t("unavailable"),
          }),
        },
        form,
      ),
    );
  }
  return { node: root, refresh, dispose: () => generation++ };
}
