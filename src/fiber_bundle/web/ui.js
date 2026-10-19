// Shared rendering primitives only; no network or business rules.
import { clearNoticeTimer, expireNotice } from "./notice_lifetime.js";
import { createOperationFeedback } from "./operation_feedback.js";
import { captureDraftSave, rememberDraft } from "./unsaved_changes.js";
import { setText, uiText, textNode } from "./i18n/index.js";
export const $ = (id) => document.getElementById(id);
export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text) {
    node.textContent = String(text);
    // Bind the initial text itself: labels/buttons may gain controls or icons later.
    if (node.firstChild) setText(node.firstChild, text);
  }
  if (className) node.className = className;
  return node;
}
export function message(text, error = false, scope = "") {
  text = uiText(text);
  const notice = $("notice");
  if (
    scope &&
    notice.dataset?.page !== undefined &&
    notice.dataset.page !== scope
  )
    return;
  const changed =
    notice.textContent !== String(text) ||
    notice.className !== (error ? "error" : "");
  setText(notice, text);
  notice.className = error ? "error" : "";
  if (notice.dataset) notice.dataset.scope = scope;
  notice.hidden = !text;
  if (text) expireNotice(notice, error, changed);
  else clearNoticeTimer(notice);
  if (text && changed) {
    animate(
      notice,
      [
        { opacity: 0, transform: "translateY(4px)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      "--duration-slow",
    );
  }
}

// Navigation/session changes invalidate feedback captured by an earlier operation.
export function setFeedbackPage(page) {
  for (const feedback of busyFeedback) feedback.clear();
  busyFeedback.clear();
  const notice = $("notice");
  if (!notice.dataset) return;
  notice.dataset.page = page || "";
  notice.dataset.generation = String(
    Number(notice.dataset.generation || 0) + 1,
  );
  if (
    page === null ||
    (notice.dataset.scope && notice.dataset.scope !== page)
  ) {
    clearNoticeTimer(notice);
    notice.textContent = "";
    notice.hidden = true;
    notice.dataset.scope = "";
  }
}
export function captureMessage(scope, operation = "") {
  const notice = $("notice"),
    generation = notice.dataset?.generation;
  const origin = scope ?? notice.dataset?.page ?? "";
  return (text, error = false) => {
    if (generation !== notice.dataset?.generation) return;
    if (!text && operation && notice.dataset?.operation !== operation) return;
    message(text, error, origin);
    if (notice.dataset && (!origin || notice.dataset.page === origin))
      notice.dataset.operation = operation;
  };
}

const activeAnimations = new WeakMap();
if (typeof window !== "undefined") {
  window
    .matchMedia("(prefers-reduced-motion: reduce)")
    .addEventListener("change", (event) => {
      if (event.matches)
        document.getAnimations().forEach((animation) => animation.cancel());
    });
}
function animate(node, frames, duration, delay = 0) {
  if (
    typeof node.animate !== "function" ||
    document.documentElement?.dataset.motion === "reduce" ||
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  )
    return;
  activeAnimations.get(node)?.cancel();
  const tokens = window.getComputedStyle(document.documentElement);
  activeAnimations.set(
    node,
    node.animate(frames, {
      duration: parseFloat(tokens.getPropertyValue(duration)),
      delay,
      easing: tokens.getPropertyValue("--ease-out").trim(),
      fill: "backwards",
    }),
  );
}

// Entrance belongs to navigation, not periodic data refresh or evidence rendering.
export function enterList(panel) {
  if (!panel || panel.hidden) return;
  const rows = panel.querySelectorAll(
    ".rows > .row, #client-permissions > .row, .batch-list > .batch-card, " +
      "#document-list > .row, #media-list > article:not(.document-review)",
  );
  const stagger = parseFloat(
    window
      .getComputedStyle(document.documentElement)
      .getPropertyValue("--stagger"),
  );

  const narrow = window.matchMedia("(max-width: 720px)").matches;
  Array.from(rows)
    .slice(0, narrow ? 2 : 4)
    .forEach((row, index) => {
      animate(
        row,
        [
          { opacity: 0, transform: `translateY(${narrow ? 2 : 4}px)` },
          { opacity: 1, transform: "translateY(0)" },
        ],
        "--duration-base",
        index * stagger,
      );
    });
}
export function showLoading(containerId, count = 2) {
  const container = $(containerId);
  container.setAttribute("aria-busy", "true");
  if (container.children.length) return;
  const skeletons = [];
  for (let index = 0; index < count; index++) {
    const placeholder = el("div", null, "skeleton");
    placeholder.setAttribute("aria-hidden", "true");
    skeletons.push(placeholder);
  }
  container.setAttribute("aria-busy", "true");
  container.replaceChildren(...skeletons);
}
export function finishLoading(containerId) {
  const container = $(containerId);
  container.removeAttribute("aria-busy");
  for (const child of Array.from(container.children || []))
    if (child.className === "skeleton") child.remove();
}
const operationFeedback = new WeakMap();
const busyFeedback = new Set();
// Busy presentation does not decide whether policy permits the next action.
export function setBusy(button, busy, kind = "backend") {
  let feedback = operationFeedback.get(button);
  if (busy) {
    button.setAttribute("aria-busy", "true");
    if (!feedback) {
      feedback = createOperationFeedback(button, { kind });
      operationFeedback.set(button, feedback);
    }
    busyFeedback.add(feedback);
    feedback.start();
  } else {
    button.removeAttribute("aria-busy");
    feedback?.clear();
    busyFeedback.delete(feedback);
  }
}
export function action(
  button,
  work,
  { restoreFocus = false, feedback = "backend" } = {},
) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    setBusy(button, true, feedback);
    const notify = captureMessage(
      button.closest?.("#workspace > section")?.id.replace("-panel", ""),
    );
    try {
      await work(notify);
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(button, false);
      button.disabled = button.dataset?.policyDisabled === "true";
      // Native modal completion may leave focus on body after the button was disabled.
      if (
        restoreFocus &&
        button.isConnected &&
        !button.disabled &&
        !button.closest("[hidden]") &&
        document.activeElement === document.body
      )
        button.focus({ preventScroll: true });
    }
  });
}
export function checkbox(text, checked) {
  const label = el("label", null, "check"),
    input = el("input");
  input.type = "checkbox";
  input.checked = !!checked;
  input.addEventListener("change", () => {
    animate(
      input,
      [{ transform: "scale(0.9)" }, { transform: "scale(1)" }],
      "--duration-fast",
    );
  });
  label.append(input, textNode(uiText(text)));
  return { label, input };
}
export function permissionForm(values, saveLabel, save) {
  const form = el("form", null, "permissions"),
    fields = {};
  form.dataset.draft = "tracked";
  for (const [key, title] of [
    ["query", "查询"],
    ["original", "取原件"],
    ["add", "新增"],
  ]) {
    const box = checkbox(title, values[key]);
    fields[key] = box.input;
    form.append(box.label);
  }
  const button = el("button", saveLabel);
  button.type = "submit";
  form.append(button);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    setBusy(button, true);
    const acknowledge = captureDraftSave(form);
    const notify = captureMessage(
      form.closest?.("#workspace > section")?.id.replace("-panel", ""),
    );
    try {
      const afterSave = await save(
        Object.fromEntries(
          Object.entries(fields).map(([key, input]) => [key, input.checked]),
        ),
      );
      acknowledge();
      if (typeof afterSave === "function") await afterSave();
      notify("权限已保存。");
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(button, false);
      button.disabled = false;
    }
  });
  rememberDraft(form);
  return form;
}
