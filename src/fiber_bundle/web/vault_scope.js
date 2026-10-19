// Page-local knowledge-base choice. Controllers retain their own draft guards and requests.
import { el, captureMessage } from "./ui.js";
import { setText, uiText } from "./i18n/index.js";

export function selectableVaults(vaults) {
  return vaults.filter(
    (v) =>
      v.status === "active" &&
      (!v.attachment_state || v.attachment_state === "attached"),
  );
}
export function createVaultScope(controllers) {
  let vaults = [],
    clients = [],
    epoch = 0;
  const pickers = new Map();
  for (const key of ["save", "layout"]) {
    const host = el("div", null, "vault-scope-picker"),
      label = el("label", uiText("知识库")),
      select = el("select"),
      status = el("p", "", "muted");
    select.dataset.draft = "ignore";
    setText(
      select,
      key === "save"
        ? uiText("保存规则使用的知识库")
        : uiText("布局与外部改动使用的知识库"),
      "aria-label",
    );
    label.append(select);
    host.append(label, status);
    const picker = { host, select, status, revision: 0 };
    pickers.set(key, picker);
    select.addEventListener("change", () => void open(key));
  }
  async function open(key) {
    const picker = pickers.get(key),
      controller = controllers[key],
      selected = vaults.find((v) => v.id === picker.select.value);
    if (!selected) return;
    const current = epoch,
      revision = ++picker.revision;
    try {
      await controller.open(selected, clients);
      if (current !== epoch || revision !== picker.revision) return;
      // The controller may decline a scope change to preserve an unsaved draft.
      if (controller.vaultId && controller.vaultId !== selected.id)
        picker.select.value = controller.vaultId;
    } catch (error) {
      if (current === epoch && revision === picker.revision)
        captureMessage("vaults")(error.message, true);
    }
  }
  function activate(key) {
    const picker = pickers.get(key);
    if (!picker) return;
    if (controllers[key].vaultId)
      picker.select.value = controllers[key].vaultId;
    if (!picker.select.value && vaults.length === 1)
      picker.select.value = vaults[0].id;
    setText(
      picker.status,
      vaults.length
        ? uiText("读取规则不会保存、启用或移动文件。")
        : uiText("暂无可用知识库，请先接入。"),
    );
    if (picker.select.value) void open(key);
  }
  return {
    host(key) {
      return pickers.get(key).host;
    },
    activate,
    setData(values, owners) {
      vaults = selectableVaults(values);
      clients = owners;
      for (const [key, picker] of pickers) {
        const selected = controllers[key].vaultId || picker.select.value;
        const placeholder = el("option", uiText("请选择知识库"));
        placeholder.value = "";
        picker.select.replaceChildren(placeholder);
        for (const vault of vaults) {
          const option = el("option", vault.name);
          option.value = vault.id;
          picker.select.append(option);
        }
        picker.select.value = vaults.some((v) => v.id === selected)
          ? selected
          : "";
      }
      if (document.querySelector('[data-tab="vaults"][aria-current="page"]'))
        activate(
          document.getElementById("vaults-panel").dataset.selectedSection,
        );
    },
    clear() {
      epoch++;
      vaults = [];
      clients = [];
      for (const picker of pickers.values()) {
        picker.revision++;
        picker.select.replaceChildren();
        setText(picker.status, "");
      }
    },
  };
}
