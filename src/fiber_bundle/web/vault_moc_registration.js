import { phrase, setText, uiText } from "./i18n/index.js";
// Existing MOC adoption always previews preserved human text before confirmation.
import { el } from "./ui.js";
import { control } from "./record_ui.js";

export function renderMocRegistration(api, host, scope, current, changed) {
  const section = el("details", null, "record-card"),
    content = el("div"),
    summary = el("summary", uiText("已有 MOC 登记接入"));
  section.append(
    summary,
    el(
      "p",
      uiText(
        "读取保存规则中 MOC 目录的现有文件。确认后保留人工内容，仅追加系统关联区；不会重新生成目录笔记或分类整库。",
      ),
    ),
    content,
  );
  host.append(section);
  let loading = false,
    ticket = 0;
  const valid = () => current() && section.isConnected;
  async function load() {
    if (loading) return;
    loading = true;
    content.replaceChildren(el("p", uiText("正在读取已有 MOC…")));
    try {
      const state = await api.mocList(scope.client_id, scope.vault_id);
      if (!valid()) return;
      content.replaceChildren();
      for (const op of state.operations) {
        content.append(
          el("p", phrase("登记待恢复：{p0}", { p0: op.path })),
          control("核对并恢复 MOC 登记", async () => {
            const result = await api.mocAction(op.id, "reconcile", scope);
            if (!valid()) return;
            if (result.state !== "completed")
              throw new Error(
                "已确认原文件未改动，请再次点击恢复以准备新的安全写入。",
              );
            await changed();
          }),
        );
      }
      const candidates = state.candidates.filter((n) => !n.moc_id);
      content.append(
        el(
          "p",
          phrase("已接入 {p0} 个；待接入 {p1} 个。", {
            p0: state.candidates.length - candidates.length,
            p1: candidates.length,
          }),
        ),
      );
      if (state.truncated)
        content.append(
          el("p", uiText("本次最多显示 1000 个 MOC 文件。"), "muted"),
        );
      if (!candidates.length) return;
      const select = el("select"),
        preview = el("div");
      setText(select, uiText("待接入的已有 MOC"), "aria-label");
      for (const n of candidates) {
        const option = el("option", n.title + " · " + n.path);
        option.value = n.path;
        select.append(option);
      }
      let pending = null;
      const confirm = control("确认登记并保留人工内容", async () => {
        if (!pending) return;
        const result = await api.mocAction(
          pending.operation_id,
          "apply",
          scope,
        );
        if (!valid()) return;
        if (result.state !== "completed")
          throw new Error("MOC 登记未完成，请核对恢复记录。");
        await changed();
      });
      confirm.disabled = true;
      select.addEventListener("change", () => {
        ticket++;
        pending = null;
        preview.replaceChildren();
        confirm.disabled = true;
      });
      content.append(
        select,
        control("预览接入已有 MOC", async () => {
          const item = candidates.find((n) => n.path === select.value),
            stamp = ++ticket;
          if (!item) throw new Error("请选择现有 MOC 文件。");
          pending = null;
          confirm.disabled = true;
          const result = await api.mocPreview({
            ...scope,
            path: item.path,
            expected_sha256: item.sha256,
          });
          if (!valid() || stamp !== ticket) return;
          pending = result;
          preview.replaceChildren(
            el("p", phrase("文件：{p0}", { p0: result.path })),
            el(
              "p",
              phrase("保留人工内容 {p0} 字节；只追加系统关联区。", {
                p0: result.manual_bytes_preserved,
              }),
            ),
            el("pre", result.markdown),
          );
          confirm.disabled = false;
        }),
        preview,
        confirm,
      );
    } catch (error) {
      if (valid())
        content.replaceChildren(
          el(
            "p",
            phrase("读取 MOC 失败：{p0}", { p0: uiText(error.message) }),
            "error",
          ),
          control("重试读取 MOC", load),
        );
    } finally {
      loading = false;
    }
  }
  section.addEventListener("toggle", () => {
    if (section.open && !content.childNodes.length) void load();
  });
}
