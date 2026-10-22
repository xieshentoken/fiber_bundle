import { h } from "./dom.js";
import { button, card, field, input, select } from "./ui.js";
import { lang } from "./i18n.js";
import { createFontPicker } from "../font_picker.js";
import { rememberDraft, captureDraftSave } from "../unsaved_changes.js";
import { run, toast } from "./feedback.js";
export function appearanceSettings(ctx) {
  const en = lang() === "en",
    form = h("form.a-form", {
      dataset: { draft: "tracked" },
      onsubmit: (e) => e.preventDefault(),
    }),
    fields = {};
  const value = ctx.appearance.current;
  for (const [name, label, choices] of [
    [
      "latinFont",
      en ? "Latin font" : "英文字体",
      [
        ["dm", "DM Sans"],
        ["system", en ? "System" : "系统字体"],
        ["serif", en ? "Serif" : "衬线"],
        ["theme", en ? "Theme" : "跟随主题"],
      ],
    ],
    [
      "cjkFont",
      en ? "Chinese font" : "中文字体",
      [
        ["noto", "Noto Sans SC"],
        ["system", en ? "System" : "系统字体"],
        ["serif", en ? "Serif" : "衬线"],
        ["theme", en ? "Theme" : "跟随主题"],
      ],
    ],
    ["latinSize", en ? "Latin size" : "英文字号", null],
    ["cjkSize", en ? "Chinese size" : "中文字号", null],
    [
      "lineHeight",
      en ? "Line spacing" : "行距",
      [
        ["normal", en ? "Normal" : "标准"],
        ["relaxed", en ? "Relaxed" : "宽松"],
      ],
    ],
  ]) {
    const node = choices
      ? select(choices, value[name])
      : input({ type: "number", min: 12, max: 20, value: value[name] });
    node.name = name;
    fields[name] = node;
    form.append(field(label, node));
  }
  const status = h("p.a-subtle"),
    read = button(en ? "Read installed fonts" : "读取本机字体"),
    save = button(en ? "Save font settings" : "保存字体设置", {
      variant: "primary",
    }),
    cancel = button(en ? "Cancel preview" : "取消预览");
  const picker = createFontPicker(form, read, status, ctx.api.systemFonts);
  picker.populate(value);
  const readValue = () => ({
    ...ctx.appearance.current,
    ...Object.fromEntries(
      Object.entries(fields).map(([k, n]) => [
        k,
        k.endsWith("Size") ? Number(n.value) : n.value,
      ]),
    ),
  });
  form.addEventListener("input", () => ctx.appearance.apply(readValue()));
  save.addEventListener("click", () =>
    run(save, async () => {
      if (!form.reportValidity()) return;
      const ack = captureDraftSave(form);
      ctx.appearance.save(readValue());
      ack();
      toast(en ? "Font settings saved." : "字体设置已保存。", {
        tone: "success",
      });
    }),
  );
  cancel.addEventListener("click", () => {
    ctx.appearance.cancel();
    for (const [name, node] of Object.entries(fields))
      node.value = ctx.appearance.current[name];
    picker.populate(ctx.appearance.current);
    rememberDraft(form);
  });
  form.append(h("div.a-toolbar", read, save, cancel), status);
  rememberDraft(form);
  return {
    node: card({ title: en ? "Fonts & readability" : "字体与阅读" }, form),
    dispose: () => ctx.appearance.cancel(),
  };
}
