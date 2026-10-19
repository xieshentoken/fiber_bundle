import { createDesktopLocaleSync } from "./desktop.js";
import { locale, setLocale, onLocaleChange, msg, setText } from "./index.js";
export function createLanguageControls() {
  const syncDesktop = createDesktopLocaleSync(globalThis.window?.fiberDesktop);
  syncDesktop(locale());
  const controls = [...document.querySelectorAll("[data-language-control]")];
  const statuses = [...document.querySelectorAll("[data-language-status]")];
  for (const control of controls) {
    control.value = locale();
    control.addEventListener("change", (event) => {
      // Locale commits are independent of the outer appearance preview form.
      event.stopPropagation();
      try {
        setLocale(control.value);
        for (const status of statuses) {
          status.hidden = true;
          status.textContent = "";
        }
      } catch {
        control.value = locale();
        for (const status of statuses) {
          setText(status, msg("common.locale_failure"));
          status.hidden = false;
        }
      }
    });
    control.addEventListener("input", (event) => event.stopPropagation());
  }
  onLocaleChange((value) => {
    syncDesktop(value);
    controls.forEach((control) => {
      control.value = value;
    });
  });
}
