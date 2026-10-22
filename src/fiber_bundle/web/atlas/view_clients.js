// Agents & clients. Each client has its own credential and per-vault grants; give only what
// is needed. New credentials are shown once and never stored by the browser.
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  checkbox,
  empty,
  failure,
  field,
  input,
  listRow,
  notice,
  skeleton,
  tabs,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";
import { createAgents } from "./clients_agents.js";

const t = strings({
  zh: {
    agents: "Agent 接入",
    clients: "客户端身份",
    create: "新建客户端",
    name: "客户端名称",
    namePlaceholder: "例如：资料整理助手",
    canCreate: "允许申请新建空库",
    submit: "创建客户端",
    created: "客户端已创建。请保存凭证，再授予所需权限。",
    token: "新凭证 · 仅本次可查看，请妥善保存",
    show: "显示",
    hide: "隐藏",
    copy: "复制",
    copied: "已复制到剪贴板。",
    copyFailed: "无法访问剪贴板，请显示后手动复制。",
    dismiss: "我已保存",
    list: "客户端",
    none: "尚未创建客户端。",
    revokedTag: "已撤销",
    active: "启用",
    pick: "选择一个客户端，分别授予各知识库权限。",
    revokedNote: "此客户端已撤销，凭证不能继续使用。",
    vault: "知识库",
    query: "查询",
    original: "取原件",
    add: "新增",
    save: "保存",
    saved: "权限已保存。",
    revoke: "撤销客户端",
    revokeTitle: "撤销“{name}”的全部权限？",
    revokeBody: "凭证立即失效，已授予的所有知识库权限一并撤销，不能恢复。",
    revoked: "客户端已撤销。",
    noVaults: "暂无启用的知识库。",
    grantsNote:
      "查询：检索笔记；取原件：下载保存的原件；新增：写入资料。三项分别授予。",
    managedHidden: "Agent 连接自带的受管身份在“Agent 接入”中管理，不在此列出。",
    discard: "当前客户端权限尚未保存，确定切换？",
  },
  en: {
    agents: "Agent connections",
    clients: "Client identities",
    create: "New client",
    name: "Client name",
    namePlaceholder: "e.g. Research assistant",
    canCreate: "May request new empty vaults",
    submit: "Create client",
    created:
      "Client created. Save the credential, then grant only what it needs.",
    token: "New credential · shown only now — store it safely",
    show: "Show",
    hide: "Hide",
    copy: "Copy",
    copied: "Copied to clipboard.",
    copyFailed:
      "Clipboard unavailable; show the credential and copy it manually.",
    dismiss: "I've saved it",
    list: "Clients",
    none: "No clients yet.",
    revokedTag: "revoked",
    active: "Active",
    pick: "Pick a client to grant access per vault.",
    revokedNote: "This client is revoked; its credential no longer works.",
    vault: "Vault",
    query: "Query",
    original: "Originals",
    add: "Add",
    save: "Save",
    saved: "Grants saved.",
    revoke: "Revoke client",
    revokeTitle: "Revoke all access for “{name}”?",
    revokeBody:
      "The credential stops working immediately and every vault grant is removed. This can't be undone.",
    revoked: "Client revoked.",
    noVaults: "No active vaults.",
    grantsNote:
      "Query: search notes · Originals: download saved originals · Add: file new material. Each is granted separately.",
    managedHidden:
      "Managed identities that belong to agent connections are handled under Agent connections.",
    discard: "This client's grants aren't saved. Switch anyway?",
  },
});

export function createView(ctx, route) {
  let sub = route.sub === "clients" ? "clients" : "agents",
    params = route.params || {},
    child = null,
    epoch = 0;
  const tabHost = h("div");
  const body = h("div.a-stack");
  function drawTabs() {
    clear(
      tabHost,
      tabs(
        [
          { value: "agents", label: t("agents") },
          { value: "clients", label: t("clients") },
        ],
        sub,
        (value) => ctx.navigate("clients", value),
        t("agents"),
      ),
    );
  }
  function clientsTab() {
    let clients = [],
      vaults = [],
      selected = params.id || "";
    const tokenHost = h("div");
    const listHost = h("div.a-rows");
    const detail = h("div.a-stack");
    const name = input({
      id: "client-name",
      required: true,
      maxLength: 100,
      placeholder: t("namePlaceholder"),
    });
    const capability = checkbox(t("canCreate"));
    const submit = h(
      "button.a-button.is-primary",
      { type: "submit" },
      icon("plus"),
      t("submit"),
    );
    const form = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      h("div.a-field-row", field(t("name"), name), submit),
      capability.label,
    );
    function clearToken() {
      tokenHost.replaceChildren();
    }
    function showToken(value) {
      const field_ = h("input.a-input.a-mono", {
        type: "password",
        readOnly: true,
        value,
        autocomplete: "off",
        "aria-label": t("token"),
      });
      const reveal = button(t("show"), { variant: "small", iconName: "eye" });
      reveal.addEventListener("click", () => {
        const visible = field_.type === "password";
        field_.type = visible ? "text" : "password";
        reveal.lastChild.textContent = visible ? t("hide") : t("show");
      });
      const copy = button(t("copy"), { variant: "small", iconName: "copy" });
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(value);
          toast(t("copied"), { tone: "success", timeout: 2000 });
        } catch {
          toast(t("copyFailed"), { tone: "warning" });
        }
      });
      clear(
        tokenHost,
        notice(
          h("strong", t("token")),
          "warning",
          h(
            "div.a-token-line",
            field_,
            reveal,
            copy,
            button(t("dismiss"), { variant: "small", onClick: clearToken }),
          ),
        ),
      );
    }
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(submit, async () => {
        clearToken();
        const acknowledge = captureDraftSave(form);
        const result = await ctx.api.createClient(
          name.value,
          capability.input.checked,
        );
        selected = result.id;
        acknowledge();
        name.value = "";
        ctx.store.invalidate("clients");
        await load();
        showToken(result.token);
        toast(t("created"), { tone: "success" });
      });
    });
    function drawList() {
      const visible = clients.filter((c) => !c.managed_connection_id);
      clear(
        listHost,
        visible.length
          ? visible.map((client) =>
              listRow({
                iconName: "key",
                title: client.name,
                detail: client.active ? t("active") : t("revokedTag"),
                status: client.active ? null : [t("revokedTag"), "info"],
                selected: client.id === selected,
                onClick: () => {
                  if (
                    hasUnsavedChanges(detail) &&
                    !window.confirm(t("discard"))
                  )
                    return;
                  selected = client.id;
                  clearToken();
                  drawList();
                  drawDetail();
                },
              }),
            )
          : empty(t("none"), { iconName: "key" }),
      );
    }
    function drawDetail() {
      const client = clients.find((c) => c.id === selected);
      if (!client) {
        clear(detail, card({}, empty(t("pick"), { iconName: "shield" })));
        return;
      }
      if (!client.active) {
        clear(
          detail,
          card({ title: client.name }, notice(t("revokedNote"), "info")),
        );
        return;
      }
      const active = vaults.filter((v) => v.status === "active");
      const rows = active.map((vault) => {
        const grant = client.grants.find((g) => g.vault_id === vault.id) || {};
        const boxes = ["query", "original", "add"].map((key) => {
          const box = h("input", {
            type: "checkbox",
            checked: !!grant["can_" + key],
            "aria-label": `${vault.name} ${t(key)}`,
          });
          return { key, box };
        });
        const save = button(t("save"), { variant: "small" });
        const row = h(
          "tr",
          { dataset: { draft: "tracked" } },
          h("th", { scope: "row" }, vault.name),
          boxes.map(({ box }) => h("td.is-center", box)),
          h("td", save),
        );
        save.addEventListener("click", () =>
          run(save, async () => {
            const acknowledge = captureDraftSave(row);
            await ctx.api.setPermissions(
              client.id,
              vault.id,
              Object.fromEntries(
                boxes.map(({ key, box }) => [key, box.checked]),
              ),
            );
            acknowledge();
            ctx.store.invalidate("clients");
            toast(t("saved"), { tone: "success" });
            const fresh = await ctx.store.clients({ force: true });
            clients = fresh;
          }),
        );
        return row;
      });
      const revoke = button(t("revoke"), {
        variant: "danger-outline",
        iconName: "trash",
      });
      revoke.addEventListener("click", () =>
        run(revoke, async () => {
          if (
            !(await confirm({
              title: t("revokeTitle", { name: client.name }),
              body: t("revokeBody"),
              confirmLabel: t("revoke"),
              tone: "danger",
            }))
          )
            return false;
          await ctx.api.revokeClient(client.id);
          clearToken();
          ctx.store.invalidate("clients");
          toast(t("revoked"), { tone: "success" });
          await load();
        }),
      );
      clear(
        detail,
        card(
          {
            title: client.name,
            subtitle: t("grantsNote"),
            actions: badge(t("active"), "success"),
          },
          active.length
            ? h(
                "div.a-table-wrap",
                h(
                  "table.a-table.a-grant-table",
                  h(
                    "thead",
                    h(
                      "tr",
                      h("th", t("vault")),
                      h("th.is-center", t("query")),
                      h("th.is-center", t("original")),
                      h("th.is-center", t("add")),
                      h("th", ""),
                    ),
                  ),
                  h("tbody", rows),
                ),
              )
            : empty(t("noVaults"), { iconName: "vaults" }),
          h("div.a-form-actions", revoke),
        ),
      );
      rememberDraft(detail);
    }
    async function load() {
      const mine = ++epoch;
      if (!clients.length) clear(listHost, skeleton(3));
      try {
        [clients, vaults] = await Promise.all([
          ctx.store.clients({ force: true }),
          ctx.store.vaults({ force: true }),
        ]);
      } catch (error) {
        clear(listHost, failure(error, load));
        return;
      }
      if (mine !== epoch) return;
      const visible = clients.filter((c) => !c.managed_connection_id);
      if (!visible.some((c) => c.id === selected))
        selected = visible.find((c) => c.active)?.id || visible[0]?.id || "";
      drawList();
      if (!hasUnsavedChanges(detail)) drawDetail();
    }
    rememberDraft(form);
    window.addEventListener("pagehide", clearToken, { once: true });
    return {
      node: h(
        "div.a-stack",
        card({ title: t("create") }, form, tokenHost),
        h(
          "div.a-split",
          h(
            "div.a-split-list",
            card({ title: t("list"), className: "is-flush" }, listHost),
            h("p.a-subtle", t("managedHidden")),
          ),
          detail,
        ),
      ),
      refresh: load,
      dirty: () => hasUnsavedChanges(detail),
      dispose: () => {
        epoch++;
        clearToken();
      },
    };
  }
  function mount() {
    child?.dispose?.();
    child = sub === "clients" ? clientsTab() : createAgents(ctx, params);
    drawTabs();
    clear(body, child.node);
    return child.refresh();
  }
  return {
    node: h("div.a-stack", tabHost, body),
    refresh: () => (child ? child.refresh() : mount()),
    update(next) {
      sub = next.sub === "clients" ? "clients" : "agents";
      params = next.params || {};
      void mount();
      return true;
    },
    dirty: () => Boolean(child?.dirty?.()),
    dispose: () => child?.dispose?.(),
  };
}
