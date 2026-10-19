// Groups existing controls; feature owners keep values, validation and listeners.
import { el } from "./ui.js";
import { uiText } from "./i18n/index.js";
export function formSection(title) {
  const host = el("fieldset", null, "form-section"),
    fields = el("div", null, "form-fields");
  host.append(el("legend", uiText(title)), fields);
  return { host, fields };
}
