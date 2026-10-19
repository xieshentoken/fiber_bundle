import { setText, uiText } from "./i18n/index.js";
// Bounded module browsing and evidence reads under the selected external connection.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { expertResults, downloadMarkdown } from "./expert_results.js";
export function knowledgePanel(item, api, canAct) {
  const root = el("details"),
    list = el("div"),
    output = el("div");
  root.append(el("summary", uiText("知识模块与来源（只读）")));
  const present = (value) => {
    if (root.isConnected) {
      output.replaceChildren();
      const content = expertResults(value);
      if (content) output.append(content);
      else output.append(el("pre", JSON.stringify(value, null, 2)));
    }
  };
  async function read(module, kind, identity) {
    canAct();
    const value = await api.agents.knowledgeEvidence(item.id, {
      module_id: module.module_id,
      expected_version: module.version,
      kind,
      identity,
    });
    present(value);
    return value;
  }
  async function catalog(offset = 0) {
    canAct();
    const value = await api.agents.knowledgeCatalog(item.id, {
      vault_ids: item.configuration.vault_ids,
      offset,
      limit: 20,
    });
    if (!root.isConnected) return;
    list.replaceChildren();
    for (const module of value.items) {
      const row = el("article", null, "record-card");
      row.append(
        el(
          "p",
          `${module.title} · ${module.kind} · ${module.completeness_status || "unknown"}`,
        ),
      );
      if (module.known_gaps?.length)
        row.append(el("p", module.known_gaps.join("；")));
      if (module.kind === "expert") {
        const objects = el("div");
        row.append(
          control("查看专家对象与证据", async () => {
            const packet = await read(module, "module", null);
            objects.replaceChildren();
            for (const object of packet.objects) {
              const block = el("div");
              block.append(
                control(object.title, () => read(module, "object", object.id), {
                  localize: false,
                }),
              );
              for (const id of object.evidence_ids)
                block.append(
                  control("证据 " + id, () => read(module, "anchor", id)),
                );
              objects.append(block);
            }
          }),
          objects,
        );
        const query = el("input");
        query.maxLength = 256;
        setText(query, uiText("专家模块查询"), "aria-label");
        row.append(
          query,
          control("查询已发布专家 Draft", async () => {
            canAct();
            present(
              await api.agents.knowledgeExpert(item.id, {
                module_id: module.module_id,
                expected_version: module.version,
                question: query.value,
                limit: 5,
                output_capability: "full",
              }),
            );
          }),
          control("下载专家回答 Markdown 与图片", async () => {
            canAct();
            downloadMarkdown(
              await api.agents.knowledgeExpert(item.id, {
                module_id: module.module_id,
                expected_version: module.version,
                question: query.value,
                limit: 5,
                output_capability: "full",
                output_format: "markdown",
              }),
            );
          }),
        );
      } else
        row.append(
          control("读取当前来源片段", () => read(module, "note", null)),
        );
      list.append(row);
    }
    if (value.next_offset !== null)
      list.append(control("下一页授权资料", () => catalog(value.next_offset)));
    if (!value.items.length)
      list.append(
        el("p", uiText("当前范围无可见模块；未校验产物可能被质量筛选排除。")),
      );
  }
  root.append(
    control("读取授权知识目录", () => catalog()),
    el(
      "p",
      uiText(
        "Draft、未校验与缺项原样保留。只读查询不调用模型，也不设置提取额度。",
      ),
    ),
    list,
    output,
  );
  return root;
}
