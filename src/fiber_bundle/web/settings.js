import { localizedDate, phrase, setText, uiText } from "./i18n/index.js";
// Appearance is client-local; fixed login policy is persisted by the authenticated API.
import { $, message, setBusy } from "./ui.js";
import { captureDraftSave, rememberDraft } from "./unsaved_changes.js";
import { DEFAULT_APPEARANCE, normalizeAppearance } from "./appearance.js";
import { themeInfo } from "./themes.js";
import { createFontPicker } from "./font_picker.js";

const formFields = Object.keys(DEFAULT_APPEARANCE).filter(
  (key) => key !== "theme" && key !== "defaultTheme",
);

export function createSettingsView(api, appearance, session, themePicker) {
  let epoch = 0,
    policy = null,
    draft = appearance.current;
  const form = $("appearance-form");
  const fonts = createFontPicker(
    form,
    $("font-refresh"),
    $("font-status"),
    api.systemFonts,
  );
  function populate(value) {
    draft = normalizeAppearance(value);
    fonts.populate(draft);
    for (const key of formFields) form.elements[key].value = draft[key];
    setText(
      $("appearance-theme-name"),
      uiText(themeInfo(appearance.current.theme).name),
    );
    form.dataset.dirty = String(
      formFields.some((key) => draft[key] !== appearance.saved[key]),
    );
  }
  function read() {
    const value = Object.fromEntries(
      formFields.map((key) => [key, form.elements[key].value]),
    );
    value.latinSize = Number(value.latinSize);
    value.cjkSize = Number(value.cjkSize);
    return normalizeAppearance({
      ...value,
      theme: appearance.current.theme,
      defaultTheme: appearance.saved.defaultTheme,
    });
  }
  appearance.subscribe(({ current, saved }) => {
    populate(current);
    // Rebase only committed fields; unsaved preview fields retain their guard baseline.
    for (const key of formFields)
      if (current[key] === saved[key]) captureDraftSave(form.elements[key])();
  });
  $("appearance-theme-change").addEventListener("click", (event) =>
    themePicker.open(event.currentTarget),
  );
  form.addEventListener("input", () => {
    draft = read();
    appearance.apply(draft);
    form.dataset.dirty = String(
      formFields.some((key) => draft[key] !== appearance.saved[key]),
    );
  });
  form.addEventListener("change", () => {
    draft = read();
    appearance.apply(draft);
    form.dataset.dirty = String(
      formFields.some((key) => draft[key] !== appearance.saved[key]),
    );
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const current = epoch,
      submitted = read(),
      acknowledge = captureDraftSave(form);
    const button = event.submitter;
    button.disabled = true;
    setBusy(button, true);
    try {
      await appearance.save(submitted);
      if (current !== epoch) return;
      draft = read();
      acknowledge();
      message("外观已保存，适用于当前浏览器或桌面客户端。", false, "settings");
    } catch {
      if (current !== epoch) return;
      message(
        "无法保存外观偏好，请检查浏览器存储权限。当前预览仍保留。",
        true,
        "settings",
      );
    } finally {
      button.disabled = false;
      setBusy(button, false);
    }
  });
  $("appearance-cancel").addEventListener("click", () => {
    populate(appearance.current);
    appearance.cancel();
    captureDraftSave(form)();
    message("已还原为保存的外观。", false, "settings");
  });
  $("appearance-reset").addEventListener("click", () => {
    populate({
      ...DEFAULT_APPEARANCE,
      theme: appearance.current.theme,
      defaultTheme: appearance.saved.defaultTheme,
    });
    appearance.apply(draft);
    form.dataset.dirty = String(
      formFields.some((key) => draft[key] !== appearance.saved[key]),
    );
    message(
      "正在预览默认字体与显示设置。当前主题保留；保存后生效。",
      false,
      "settings",
    );
  });
  $("session-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!policy) return;
    const current = epoch,
      button = event.submitter;
    const acknowledge = captureDraftSave($("session-settings-form"));
    button.disabled = true;
    setBusy(button, true);
    try {
      const saved = await api.saveSystemSettings({
        session_ttl: Number($("session-ttl").value),
        expected_revision: policy.revision,
      });
      if (current !== epoch) return;
      policy = saved;
      acknowledge();
      message(
        "登录时长已保存，下次登录生效；当前会话到期时间保持不变。",
        false,
        "settings",
      );
    } catch (error) {
      if (current === epoch)
        message(
          error.status === 409
            ? "设置已在其他窗口更新，请重新进入本页核对后再保存。"
            : error.message,
          true,
          "settings",
        );
    } finally {
      setBusy(button, false);
      button.disabled = !policy;
    }
  });
  return {
    get hasUnsavedChanges() {
      return formFields.some((key) => draft[key] !== appearance.saved[key]);
    },
    async refresh() {
      const current = ++epoch;
      populate(appearance.current);
      fonts.refresh(false);
      $("session-settings-save").disabled = true;
      const result = await api.systemSettings();
      if (current !== epoch) return;
      policy = result;
      const select = $("session-ttl");
      select.querySelector("[data-custom-ttl]")?.remove();
      if (
        ![...select.options].some(
          (option) => Number(option.value) === policy.session_ttl,
        )
      ) {
        const option = document.createElement("option");
        option.value = policy.session_ttl;
        setText(
          option,
          phrase("{p0} 秒（启动配置）", { p0: policy.session_ttl }),
        );
        option.dataset.customTtl = "true";
        select.append(option);
      }
      select.value = policy.session_ttl;
      setText(
        $("session-current-expiry"),
        phrase("当前会话到期：{p0}", {
          p0: localizedDate(session.expiresAt * 1000, {
            dateStyle: "medium",
            timeStyle: "medium",
          }),
        }),
      );
      $("session-settings-save").disabled = false;
      rememberDraft($("settings-panel"));
    },
    clear() {
      epoch++;
      policy = null;
      draft = appearance.current;
      appearance.cancel();
    },
  };
}
