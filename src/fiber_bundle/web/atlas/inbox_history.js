// Completed records remain in the source response; this is a local view filter only.
import { checkbox } from "./ui.js";
import { lang } from "./i18n.js";
import { isInboxComplete } from "../inbox_status.js";

export function createInboxHistory(onChange, explicitId) {
  const control = checkbox(
    lang() === "en" ? "Show completed history" : "显示已完成记录",
  );
  let admitted = !explicitId;
  control.input.dataset.inboxHistory = "true";
  control.input.addEventListener("change", () => {
    if (onChange() === false) control.input.checked = !control.input.checked;
  });
  return {
    node: control.label,
    filter(rows) {
      if (!admitted) {
        const selected = rows.find(
          (row) => (row.id || row.run_id || row.job_id) === explicitId,
        );
        if (selected) {
          control.input.checked = isInboxComplete(selected);
          admitted = true;
        }
      }
      return rows.filter(
        (row) => control.input.checked || !isInboxComplete(row),
      );
    },
  };
}
