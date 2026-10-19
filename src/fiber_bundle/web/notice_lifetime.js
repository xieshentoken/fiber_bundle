// A timer belongs to one visible notice. A replaced notice cannot be cleared by an old timer.
const timers = new WeakMap();
export const NOTICE_DURATION = { success: 5000, error: 10000 };
export function clearNoticeTimer(node) {
  const timer = timers.get(node);
  if (timer) clearTimeout(timer);
  timers.delete(node);
}
export function expireNotice(node, error, reset = true) {
  if (!reset && timers.has(node)) return;
  clearNoticeTimer(node);
  if (!node.textContent) return;
  const timer = setTimeout(() => {
    if (timers.get(node) !== timer) return;
    timers.delete(node);
    node.textContent = "";
    node.hidden = true;
    node.className = "";
    if (node.dataset) node.dataset.scope = "";
  }, error ? NOTICE_DURATION.error : NOTICE_DURATION.success);
  timer.unref?.();
  timers.set(node, timer);
}
