import { phrase, setText, uiText } from "./i18n/index.js";
import { $, el, permissionForm } from "./ui.js";

export function renderPending(vaults, clients, api, refreshData) {
  const target = $("pending-list");
  target.replaceChildren();
  const pending = vaults.filter((v) => v.status === "pending");
  if (!pending.length)
    target.append(el("p", uiText("没有待审批的空库。"), "empty"));
  for (const vault of pending) {
    const row = el("article", null, "ui-card approval-card"),
      info = el("div", null, "ui-task-main");
    row.dataset.taskKind = "vault";
    row.dataset.taskId = vault.id;
    const requester = clients.find(
      (c) => c.id === vault.requested_by_client_id,
    );
    info.append(
      el("strong", vault.name),
      el(
        "small",
        phrase("申请方：{p0}", { p0: requester?.name || "已不可用" }),
      ),
    );
    const group = el("div"),
      select = el("select");
    setText(
      select,
      phrase("{p0} 授权客户端", { p0: vault.name }),
      "aria-label",
    );
    for (const c of clients.filter((c) => c.active)) {
      const option = el("option", c.name);
      option.value = c.id;
      option.selected = c.id === vault.requested_by_client_id;
      select.append(option);
    }
    const form = permissionForm({}, "批准并启用", async (values) => {
      if (!select.value) throw new Error("请先创建有效客户端。");
      await api.approveVault(vault.id, select.value, values);
      await refreshData();
    });
    form.prepend(select);
    group.append(form);
    row.append(info, group);
    target.append(row);
  }
}
