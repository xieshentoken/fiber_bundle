import { joinText, setText, translate, uiText } from "./i18n/index.js";
import {
  $,
  el,
  action,
  permissionForm,
  captureMessage,
  setBusy,
} from "./ui.js";
import { hasUnsavedChanges, captureDraftSave } from "./unsaved_changes.js";

export function createClientsView(api, refreshData) {
  let vaults = [],
    clients = [],
    selected = "",
    epoch = 0,
    preservedForms = new Map(),
    permissionSnapshot = null;
  function clearToken() {
    epoch++;
    $("new-token").value = "";
    $("new-token").type = "password";
    $("token-panel").hidden = true;
    setText($("reveal-token"), uiText("显示凭证"));
  }
  function renderPermissions(force = false) {
    const target = $("client-permissions");
    const client = clients.find((c) => c.id === selected);
    const snapshot = JSON.stringify([client, vaults]);
    // Keep in-progress inputs only while the server identity/grants remain the same.
    // A revoked client or changed grant must immediately replace the old form.
    if (!force && snapshot === permissionSnapshot) return;
    permissionSnapshot = snapshot;
    target.replaceChildren();
    if (!client) {
      target.append(
        el(
          "p",
          uiText("创建客户端后，可在这里分别授予各知识库权限。"),
          "empty",
        ),
      );
      return;
    }
    if (!client.active) {
      target.append(
        el("p", uiText("此客户端已撤销，凭证不能继续使用。"), "empty"),
      );
      return;
    }
    const heading = el("div", null, "client-heading");
    heading.append(el("h3", client.name));
    const revoke = el("button", uiText("撤销客户端"), "danger");
    revoke.type = "button";
    action(revoke, async (notify) => {
      if (revoke.dataset.confirmed !== "yes") {
        revoke.dataset.confirmed = "yes";
        setText(revoke, uiText("确认撤销全部权限"));
        const cancel = el("button", uiText("取消"));
        cancel.type = "button";
        cancel.addEventListener("click", () => renderPermissions(true));
        heading.append(cancel);
        return;
      }
      await api.revokeClient(client.id);
      clearToken();
      await refreshData();
      notify("客户端已撤销。");
    });
    heading.append(revoke);
    target.append(heading);
    for (const vault of vaults.filter((v) => v.status === "active")) {
      const grant = client.grants.find((g) => g.vault_id === vault.id) || {},
        row = el("div", null, "row permission-row");
      row.append(
        el("h3", vault.name),
        preservedForms.get(client.id + ":" + vault.id) ||
          permissionForm(
            {
              query: grant.can_query,
              original: grant.can_original,
              add: grant.can_add,
            },
            "保存权限",
            async (values) => {
              const current = epoch;
              await api.setPermissions(client.id, vault.id, values);
              return async () => {
                if (current !== epoch) return;
                const form = row.children[1];
                if (hasUnsavedChanges(form))
                  preservedForms.set(client.id + ":" + vault.id, form);
                try {
                  await refreshData();
                } finally {
                  preservedForms.clear();
                }
              };
            },
          ),
      );
      target.append(row);
    }
  }

  function render(nextVaults, nextClients) {
    vaults = nextVaults;
    clients = nextClients;
    if (!clients.some((c) => c.id === selected))
      selected = clients.find((c) => c.active)?.id || clients[0]?.id || "";
    const select = $("client-select");
    select.replaceChildren();
    for (const c of clients) {
      const option = el(
        "option",
        joinText([c.name, c.active ? "" : uiText("（已撤销）")]),
      );
      option.value = c.id;
      option.selected = c.id === selected;
      select.append(option);
    }
    renderPermissions();
  }
  $("create-client").addEventListener("submit", async (event) => {
    event.preventDefault();
    event.submitter.disabled = true;
    setBusy(event.submitter, true);
    clearToken();
    preservedForms.clear();
    const revision = epoch,
      acknowledge = captureDraftSave($("create-client")),
      notify = captureMessage("clients");
    try {
      const result = await api.createClient(
        $("client-name").value,
        $("create-capability").checked,
      );
      if (revision !== epoch) return;
      selected = result.id;
      await refreshData();
      if (revision !== epoch) return;
      $("new-token").value = result.token;
      $("token-panel").hidden = false;
      acknowledge();
      notify("客户端已创建。请保存凭证，再授予所需权限。");
    } catch (error) {
      if (revision === epoch) notify(error.message, true);
    } finally {
      setBusy(event.submitter, false);
      event.submitter.disabled = false;
    }
  });
  $("client-select").addEventListener("change", () => {
    if (
      hasUnsavedChanges($("client-permissions")) &&
      !window.confirm(translate("当前客户端权限尚未保存，确定切换客户端？"))
    ) {
      $("client-select").value = selected;
      return;
    }
    selected = $("client-select").value;
    clearToken();
    renderPermissions();
  });
  $("reveal-token").addEventListener("click", () => {
    const show = $("new-token").type === "password";
    $("new-token").type = show ? "text" : "password";
    setText($("reveal-token"), show ? uiText("隐藏凭证") : uiText("显示凭证"));
  });
  $("dismiss-token").addEventListener("click", clearToken);

  function clear() {
    preservedForms.clear();
    clearToken();
    permissionSnapshot = null;
    vaults = [];
    clients = [];
    selected = "";
    $("client-permissions").replaceChildren();
    $("client-select").replaceChildren();
  }
  return { render, clear, clearToken };
}
