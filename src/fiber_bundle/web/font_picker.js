import { joinText, phrase, setText, uiText } from "./i18n/index.js";
// Installed-font choices are ephemeral; only the selected names enter appearance storage.
import { fontCatalog, localFont } from "./local_fonts.js";

export function createFontPicker(form, button, status, readFonts) {
  const selects = [form.elements.cjkFont, form.elements.latinFont];
  const searches = new Map();
  const recent = [];
  for (const select of selects) {
    const search = document.createElement("input");
    search.type = "search";
    setText(search, uiText("筛选本机字体…"), "placeholder");
    setText(
      search,
      select.name === "cjkFont"
        ? uiText("筛选中文字体")
        : uiText("筛选英文字体"),
      "aria-label",
    );
    search.dataset.draft = "ignore";
    select.before(search);
    searches.set(select, search);
    search.addEventListener("input", (event) => {
      event.stopPropagation();
      populate();
    });
    search.addEventListener("change", (event) => event.stopPropagation());
    select.addEventListener("change", () => {
      const index = recent.indexOf(select.value);
      if (index >= 0) recent.splice(index, 1);
      recent.unshift(select.value);
      recent.splice(5);
    });
  }
  let catalog = [],
    state = "unread",
    pending = null;
  function populate(value = {}) {
    for (const select of selects) {
      const selected = value[select.name] ?? select.value;
      select
        .querySelectorAll("[data-local-fonts]")
        .forEach((node) => node.remove());
      if (catalog.length) {
        const group = document.createElement("optgroup");
        setText(group, uiText("本机已安装字体"), "label");
        group.dataset.localFonts = "true";
        const query = searches.get(select).value.trim().toLocaleLowerCase();
        const ordered = [...catalog].sort((a, b) => {
          const rank = (value) =>
            recent.includes(value) ? recent.indexOf(value) : 100;
          return rank(a.value) - rank(b.value);
        });
        for (const font of ordered) {
          if (
            font.value !== selected &&
            !font.family.toLocaleLowerCase().includes(query)
          )
            continue;
          const option = document.createElement("option");
          option.value = font.value;
          setText(
            option,
            uiText(
              font.family + (recent.includes(font.value) ? " · 最近选择" : ""),
            ),
          );
          group.append(option);
        }
        select.append(group);
      }
      const saved = localFont(selected);
      if (saved && !catalog.some((font) => font.value === selected)) {
        const option = document.createElement("option");
        option.dataset.localFonts = "true";
        option.value = selected;
        setText(
          option,
          joinText([
            "",
            saved.family,
            " · ",
            state === "loaded"
              ? uiText("本机未找到，使用回退")
              : uiText("已保存"),
            "",
          ]),
        );
        select.append(option);
      }
      select.value = selected;
    }
  }
  function refresh(explicit = true) {
    if (pending) return pending;
    if (!explicit && state !== "unread") return Promise.resolve();
    state = "loading";
    button.disabled = true;
    setText(status, uiText("正在读取本机已安装字体…"));
    let timer;
    pending = Promise.race([
      Promise.resolve().then(readFonts),
      new Promise((_, reject) => {
        timer = window.setTimeout(
          () => reject(new Error("font_timeout")),
          8000,
        );
      }),
    ])
      .then((result) => {
        if (!result.supported) {
          state = "unsupported";
          setText(
            status,
            uiText("当前系统不支持读取本机字体。内置字体仍可选择。"),
          );
          return;
        }
        catalog = fontCatalog(result.fonts);
        state = "loaded";
        populate();
        setText(
          status,
          phrase(
            "已读取 {p0} 个本机字体家族，中英文可分别选择。缺少的字形由内置字体补足。",
            { p0: catalog.length },
          ),
        );
        setText(button, uiText("刷新本机字体"));
      })
      .catch((error) => {
        state = "error";
        setText(
          status,
          error.message === "font_timeout"
            ? uiText("读取本机字体超时，请重试。现有字体选择保留。")
            : uiText(
                "暂时无法读取本机字体，请点击“读取本机字体”重试。现有字体选择保留。",
              ),
        );
      })
      .finally(() => {
        window.clearTimeout(timer);
        button.disabled = state === "unsupported";
        pending = null;
      });
    return pending;
  }
  button.addEventListener("click", () => refresh());
  return { populate, refresh };
}
