import { sourceText, phrase, setText, uiText } from "./i18n/index.js";
// Whole-Vault merge wizard: explicit historical scope and audience, then concrete frozen mapping.
import { el, checkbox } from "./ui.js";
import { control } from "./record_ui.js";
import { mergeOperations } from "./vault_merge_operations.js";
import { captureDraftSave, rememberDraft } from "./unsaved_changes.js";

export function vaultMergeView(target, api) {
  const root = el("details"),
    content = el("div");
  root.append(el("summary", uiText("合并其他知识库到此库")), content);
  let loaded = false,
    generation = 0;
  root.addEventListener("toggle", async () => {
    if (!root.open || loaded) return;
    loaded = true;
    const scope = ++generation;
    try {
      const [vaults, clients, channels] = await Promise.all([
        api.vaults(),
        api.clients(),
        api.channels(),
      ]);
      if (!root.isConnected || scope !== generation) return;
      const form = el("form"),
        output = el("div"),
        owner = el("select"),
        label = el("label", uiText("执行身份")),
        sourceBoxes = {},
        history = {},
        audience = {},
        switches = {};
      let selectionRevision = 0;
      const operations = mergeOperations(target, api);
      const historyState = el("input");
      historyState.type = "hidden";
      historyState.name = "historical_selection";
      historyState.value = "{}";
      form.append(historyState);
      function historyChanged() {
        historyState.value = JSON.stringify(
          Object.fromEntries(
            Object.entries(history).map(([k, v]) => [k, [...v].sort()]),
          ),
        );
        form.dispatchEvent(new window.Event("change", { bubbles: true }));
      }
      form.addEventListener("change", () => {
        selectionRevision++;
        output.replaceChildren(el("p", uiText("选择已改变，请重新预检。")));
      });
      form.className = "uv-merge-form";
      form.dataset.draft = "tracked";
      form.addEventListener("submit", (e) => e.preventDefault());
      owner.append(
        el(
          "option",
          uiText("选择已具有源库和目标库查询、原件、新增权限的身份"),
        ),
      );
      owner.firstChild.value = "";
      for (const c of clients.filter(
        (c) => c.active && !c.managed_connection_id,
      )) {
        const option = el("option", c.name);
        option.value = c.id;
        owner.append(option);
      }
      label.append(owner);
      form.append(
        el(
          "p",
          uiText(
            "只读预检覆盖全部受管发布、来源与 MOC。历史笔记和附件须明确选择；冻结仅保存方案，执行还需完整备份及确认。源文件保留。",
          ),
        ),
        label,
        el("h4", uiText("选择源库")),
      );
      for (const v of vaults.filter(
        (v) => v.id !== target.id && v.status === "active" && !v.read_only,
      )) {
        const choice = checkbox(sourceText(v.name), false),
          area = el("div");
        sourceBoxes[v.id] = choice.input;
        history[v.id] = new Set();
        const load = control("选择历史笔记与附件", async () => {
          const result = await api.vaultMerges.inventory(v.id, owner.value);
          if (!root.isConnected || scope !== generation) return;
          const files = result.files.filter((f) => !f.managed),
            list = el("div");
          let page = 0;
          function render() {
            list.replaceChildren(
              el(
                "p",
                phrase("共 {p0} 个历史文件；已选 {p1} 个。受管资料始终纳入。", {
                  p0: files.length,
                  p1: history[v.id].size,
                }),
              ),
            );
            for (const f of files.slice(page * 100, (page + 1) * 100)) {
              const box = checkbox(
                sourceText(f.path),
                history[v.id].has(f.path),
              );
              box.input.addEventListener("change", () => {
                if (box.input.checked) history[v.id].add(f.path);
                else history[v.id].delete(f.path);
                historyChanged();
              });
              list.append(box.label);
            }
            if (page > 0)
              list.append(
                control("上一页", async () => {
                  page--;
                  render();
                }),
              );
            if ((page + 1) * 100 < files.length)
              list.append(
                control("下一页", async () => {
                  page++;
                  render();
                }),
              );
          }
          area.replaceChildren(
            control("明确选中全部历史文件", async () => {
              for (const f of files) history[v.id].add(f.path);
              historyChanged();
              render();
            }),
            control("清除历史选择", async () => {
              history[v.id].clear();
              historyChanged();
              render();
            }),
            list,
          );
          render();
        });
        form.append(choice.label, load, area);
      }
      form.append(
        el("h4", uiText("合并后目标库权限（未选身份会暂停目标库权限）")),
      );
      for (const c of clients.filter((c) => c.active)) {
        const row = el("div"),
          query = checkbox(c.name + " · 查询", false),
          original = checkbox("原件", false),
          add = checkbox("新增", false);
        if (c.managed_connection_id) {
          original.input.disabled = true;
          add.input.disabled = true;
        }
        audience[c.id] = {
          query: query.input,
          original: original.input,
          add: add.input,
        };
        row.append(query.label, original.label, add.label);
        form.append(row);
      }
      owner.addEventListener("change", () => {
        const c = audience[owner.value];
        if (c) for (const box of Object.values(c)) box.checked = true;
      });
      form.append(
        el("h4", uiText("入口切换（相关通道会暂停，完成后由用户重新启动）")),
      );
      for (const c of channels) {
        const box = checkbox(
          sourceText(c.settings?.name || c.value?.name || c.id),
          false,
        );
        switches[c.id] = box.input;
        form.append(box.label);
      }
      const defaultBox = checkbox("设为默认查询库", true);
      form.append(defaultBox.label);
      const preview = control("只读预检合并方案", async () => {
        const observedRevision = selectionRevision;
        const selection = {
          client_id: owner.value,
          source_vault_ids: Object.keys(sourceBoxes).filter(
            (k) => sourceBoxes[k].checked,
          ),
          target_vault_id: target.id,
          historical_files: Object.fromEntries(
            Object.entries(history).map(([k, v]) => [k, [...v].sort()]),
          ),
          target_grants: Object.entries(audience)
            .filter(([, v]) => Object.values(v).some((b) => b.checked))
            .map(([client_id, v]) => ({
              client_id,
              ...Object.fromEntries(
                Object.entries(v).map(([k, b]) => [k, b.checked]),
              ),
            })),
          switch_channel_ids: Object.keys(switches).filter(
            (k) => switches[k].checked,
          ),
          switch_default: defaultBox.input.checked,
        };
        const acknowledge = captureDraftSave(form);
        const packet = await api.vaultMerges.preview(selection);
        if (
          !root.isConnected ||
          scope !== generation ||
          observedRevision !== selectionRevision
        )
          return;
        output.replaceChildren(
          el(
            "h4",
            packet.ready
              ? uiText("预检通过，尚未执行")
              : uiText("存在冲突，尚未执行"),
          ),
          el(
            "p",
            phrase(
              "{p0} 个文件，含原件共 {p1} 字节；保留 {p2} 个未选择历史文件。",
              {
                p0: packet.files.length,
                p1: packet.copy_bytes.toLocaleString(),
                p2: packet.excluded.length,
              },
            ),
          ),
        );
        for (const conflict of packet.conflicts.slice(0, 200))
          output.append(
            el(
              "p",
              `${conflict.source_path || conflict.path || ""} · ${conflict.reason}${conflict.reference ? " · " + conflict.reference : ""}`,
              "error",
            ),
          );
        const mapping = el("details");
        mapping.append(el("summary", uiText("查看路径映射")));
        for (const f of packet.files.slice(0, 200))
          mapping.append(el("p", `${f.source_path} → ${f.target_path}`));
        if (packet.files.length > 200)
          mapping.append(
            el("p", uiText("这里只展示前 200 项；完整冻结方案保留全部清单。")),
          );
        output.append(mapping);
        const permissions = el("details");
        permissions.append(el("summary", uiText("核对合并后权限与入口")));
        for (const grant of packet.target_grants)
          permissions.append(
            el(
              "p",
              phrase("{p0}：查询{p1}，原件{p2}，新增{p3}", {
                p0:
                  clients.find((c) => c.id === grant.client_id)?.name ||
                  grant.client_id,
                p1: grant.query ? uiText("允许") : uiText("暂停"),
                p2: grant.original ? uiText("允许") : uiText("暂停"),
                p3: grant.add ? uiText("允许") : uiText("暂停"),
              }),
            ),
          );
        permissions.append(
          el(
            "p",
            phrase(
              "将暂停 {p0} 个相关通道，切换 {p1} 个通道；相关 Agent 需重新应用配置。未列出的目标身份权限将暂停。",
              {
                p0: packet.paused_channels.length,
                p1: packet.switch_channel_ids.length,
              },
            ),
          ),
        );
        output.append(permissions);
        if (packet.ready)
          output.append(
            control("冻结当前完整方案（不复制）", async () => {
              const frozen = await api.vaultMerges.freeze(
                selection,
                packet.plan_sha256,
              );
              acknowledge();
              if (root.isConnected && scope === generation) {
                output.append(
                  el(
                    "p",
                    phrase("方案已冻结：{p0}。请在下方核对完整备份后执行。", {
                      p0: frozen.id,
                    }),
                  ),
                );
                await operations.refresh();
              }
            }),
          );
      });
      content.replaceChildren(form, preview, output, operations.root);
      await operations.refresh();
      rememberDraft(form);
    } catch (error) {
      if (root.isConnected && scope === generation) {
        setText(content, uiText(error.message));
        loaded = false;
      }
    }
  });
  return root;
}
