import { phrase, setText, uiText } from "./i18n/index.js";
import { isConfirming } from "./confirm_dialog.js";
// Waiting feedback describes the HTTP operation, never invented backend progress.
export function createOperationFeedback(
  button,
  {
    kind = "backend",
    now = Date.now,
    schedule = setTimeout,
    cancel = clearTimeout,
    confirming = isConfirming,
  } = {},
) {
  let timer = null,
    status = null,
    generation = 0,
    running = false;
  function clear() {
    generation++;
    running = false;
    if (timer !== null) cancel(timer);
    timer = null;
    status?.remove();
    status = null;
  }
  return {
    start() {
      if (running) return;
      const panel = button.closest?.("section");
      if (!panel || panel.hidden) return;
      running = true;
      let started = now();
      const current = ++generation,
        label = button.textContent.trim();
      const show = () => {
        timer = null;
        if (current !== generation) return;
        if (!button.isConnected || panel.hidden) {
          clear();
          return;
        }
        // Nothing has been submitted while its confirmation is still open.
        if (confirming()) {
          started = now();
          timer = schedule(show, 300);
          return;
        }
        status = document.createElement("p");
        status.className = "operation-status muted";
        const description = document.createElement("span"),
          elapsed = document.createElement("span");
        description.setAttribute("role", "status");
        setText(
          description,
          phrase(
            kind === "directory"
              ? "{p0}：请在系统窗口中选择文件夹或取消；尚未保存设置。"
              : "{p0}：等待后台确认。离页不会取消已提交操作。",
            {
              p0: uiText(label),
            },
          ),
        );
        elapsed.setAttribute("aria-hidden", "true");
        status.append(description, elapsed);
        button.insertAdjacentElement("afterend", status);
        function update() {
          if (current !== generation) return;
          if (!button.isConnected || panel.hidden) {
            clear();
            return;
          }
          setText(
            elapsed,
            phrase(" 已等待 {p0} 秒。", {
              p0: Math.floor((now() - started) / 1000),
            }),
          );
          timer = schedule(update, 1000);
        }
        update();
      };
      timer = schedule(show, 300);
    },
    clear,
  };
}
