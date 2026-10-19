import { setText, uiText } from "./i18n/index.js";
// Access material generation and read-only trials do not invoke a model or install a host.
import { el } from "./ui.js";
import { control } from "./record_ui.js";

export function accessPanel(item, api, canAct) {
  const root = el("details"),
    body = el("div"),
    output = el("pre");
  root.append(el("summary", uiText("接入 Skill、安装与试查知识")), body);
  const download = el("a", uiText("下载 Fiber 接入 Skill"));
  download.href =
    "/admin/agent-connections/" + encodeURIComponent(item.id) + "/access-skill";
  download.download = "Fiber-access-skill.zip";
  body.append(
    download,
    el(
      "p",
      uiText(
        "Skill 不含 Key、Token 或整库正文。外部宿主模型费用由宿主管理，本应用未知；下载与工具检查不代表宿主已加载。",
      ),
    ),
  );
  body.append(
    control("查看安装步骤", async () => {
      canAct();
      const value = await api.agents.accessMaterials(item.id);
      if (root.isConnected) setText(output, uiText(value.installation));
    }),
  );
  const form = el("form"),
    label = el("label", uiText("只读试查（不调用模型）")),
    query = el("input"),
    button = el("button", uiText("试查知识"));
  query.required = true;
  query.maxLength = 200;
  setText(query, uiText("只读知识查询"), "aria-label");
  label.append(query);
  button.type = "submit";
  form.append(label, button);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    try {
      canAct();
      const result = await api.agents.previewKnowledge(item.id, {
        query: query.value,
        limit: 5,
      });
      if (root.isConnected)
        setText(output, uiText(JSON.stringify(result, null, 2)));
    } catch (error) {
      if (root.isConnected) setText(output, uiText(error.message));
    } finally {
      button.disabled = false;
    }
  });
  body.append(form, output);
  return root;
}
