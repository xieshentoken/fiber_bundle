import { phrase, uiText } from "./i18n/index.js";
// Durable operation controls are separate from the read-only mapping/selection wizard.
import { el, checkbox } from "./ui.js";
import { control } from "./record_ui.js";

const labels = {
  prepared: "方案已冻结，等待完整备份与执行确认",
  pausing: "暂停相关入口",
  copying: "复制并校验文件",
  originals: "复制来源原件",
  rebinding: "绑定笔记身份、来源与 MOC",
  switching: "切换权限与入口",
  index: "更新目标库索引",
  complete: "合并完成，相关通道与 Agent 保持暂停",
  reconcile: "已停止，等待核对或回退",
  rolling_back: "正在回退",
  rolled_back: "已回退，来源及审计保留",
  cancelled: "方案已取消",
};

export function mergeOperations(target, api) {
  const root = el("section"),
    list = el("div");
  let generation = 0;
  async function refresh() {
    const scope = ++generation;
    const values = await api.vaultMerges.list();
    if (!root.isConnected || generation !== scope) return;
    list.replaceChildren();
    for (const value of values.filter((v) => v.target_vault_id === target.id)) {
      const row = el("article"),
        details = el("details");
      row.append(
        el("h4", uiText(labels[value.state] || "等待核对")),
        el(
          "p",
          phrase("文件 {p0}/{p1}；源文件保留；完整备份{p2}。", {
            p0: value.copied,
            p1: value.files,
            p2: value.backup_verified ? uiText("已校验") : uiText("尚未校验"),
          }),
        ),
      );
      details.append(
        el("summary", uiText("操作标识与核对信息")),
        el("p", value.id),
        el("p", value.plan_sha256),
      );
      if (value.reason) details.append(el("p", uiText(value.reason)));
      row.append(details);
      if (value.state === "prepared") {
        const form = el("form"),
          path = el("input"),
          password = el("input"),
          confirm = checkbox(
            "我已核对冻结的文件、目标权限和入口变更；执行此方案",
            false,
          );
        form.addEventListener("submit", (e) => e.preventDefault());
        path.type = "text";
        path.autocomplete = "off";
        password.type = "password";
        password.autocomplete = "new-password";
        const pathLabel = el("label", uiText("完整加密备份目录")),
          passwordLabel = el("label", uiText("备份密码（仅本次校验）"));
        pathLabel.append(path);
        passwordLabel.append(password);
        const start = control("校验备份并开始合并", async () => {
          if (!confirm.input.checked)
            throw new Error("请先核对并勾选执行确认。");
          const backup_password = password.value;
          password.value = "";
          await api.vaultMerges.start(value.id, {
            plan_sha256: value.plan_sha256,
            backup_path: path.value,
            backup_password,
          });
          await refresh();
        });
        form.append(
          el(
            "p",
            uiText(
              "先在备份页面创建包含全部源库、目标库和应用状态的完整加密备份。密码不会保存到方案。",
            ),
          ),
          pathLabel,
          passwordLabel,
          confirm.label,
          start,
        );
        row.append(form);
      }
      if (
        !["complete", "cancelled", "rolled_back", "rolling_back"].includes(
          value.state,
        )
      )
        row.append(
          control("停止后续阶段", async () => {
            await api.vaultMerges.action(value.id, "cancel", value.plan_sha256);
            await refresh();
          }),
        );
      if (
        value.state === "reconcile" &&
        !value.cancel_requested &&
        !value.rollback_started
      )
        row.append(
          control("校验现场并继续冻结方案", async () => {
            await api.vaultMerges.action(value.id, "resume", value.plan_sha256);
            await refresh();
          }),
        );
      if (["reconcile", "complete", "rolling_back"].includes(value.state)) {
        const consent = checkbox(
          "回退此操作：仅删除本次创建且未修改的副本，保留源文件与审计",
          false,
        );
        row.append(
          consent.label,
          control("校验并回退", async () => {
            if (!consent.input.checked)
              throw new Error("请先勾选回退确认。修改过的文件会阻止回退。");
            await api.vaultMerges.action(
              value.id,
              "rollback",
              value.plan_sha256,
            );
            await refresh();
          }),
        );
      }
      list.append(row);
    }
    if (!list.childNodes.length)
      list.append(el("p", uiText("此库尚无合并操作。")));
  }
  root.append(
    el("h3", uiText("合并进度与恢复")),
    control("刷新合并进度", refresh),
    list,
  );
  return { root, refresh };
}
