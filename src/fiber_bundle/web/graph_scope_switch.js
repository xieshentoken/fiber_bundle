// Scope-select transactions keep the committed scope visible until explicit consent.
import { sameGraphScope } from "./graph_scope_preferences.js";
import { msg } from "./i18n/index.js";
export function createGraphScopeSwitch({
  current,
  restore,
  apply,
  dirty,
  busy,
  authorized,
  confirm,
  name,
  blocked = () => {},
  onError = () => {},
}) {
  let generation = 0,
    pending = null,
    previous = null;
  return {
    request(target) {
      const committed = current(),
        before = committed || previous;
      // select_controls already committed its tentative value and emitted input/change.
      // Restore synchronously, before any dialog or asynchronous work can observe it.
      if (before) restore(before);
      if (pending) return pending.promise;
      if ((committed && sameGraphScope(before, target)) || !authorized(target))
        return Promise.resolve(false);
      if (busy()) {
        blocked();
        return Promise.resolve(false);
      }
      if (!committed && dirty()) {
        onError(new Error(String(msg("graph_scope.switch_unavailable"))));
        return Promise.resolve(false);
      }
      if (!dirty()) {
        previous = { ...target };
        return Promise.resolve(apply(target));
      }
      const turn = generation,
        controller = new AbortController();
      const valid = () =>
        turn === generation &&
        !controller.signal.aborted &&
        sameGraphScope(current(), before) &&
        authorized(before) &&
        authorized(target) &&
        !busy();
      const operation = { controller };
      const promise = (async () => {
        let accepted;
        try {
          accepted = await confirm({
            title: String(msg("graph_scope.switch_title")),
            body: [
              String(
                msg("graph_scope.switch_body", {
                  p0: name(before),
                  p1: name(target),
                }),
              ),
            ],
            confirmLabel: String(msg("graph_scope.switch_accept")),
            cancelLabel: String(msg("graph_scope.switch_cancel")),
            tone: "danger",
            signal: controller.signal,
          });
        } catch (error) {
          if (turn === generation && !controller.signal.aborted)
            onError(
              new Error(String(msg("graph_scope.switch_failed")), {
                cause: error,
              }),
            );
          return false;
        }
        // A native fallback's undefined or other truthy value is never consent.
        if (accepted !== true || !valid()) return false;
        if (pending === operation) pending = null;
        previous = { ...target };
        return apply(target);
      })().finally(() => {
        if (pending === operation) pending = null;
      });
      operation.promise = promise;
      pending = operation;
      return promise;
    },
    remember(value) {
      previous = value ? { ...value } : null;
    },
    isPending: () => !!pending,
    cancel({ forget = false } = {}) {
      if (forget) previous = null;
      generation++;
      const old = pending;
      pending = null;
      old?.controller.abort();
    },
  };
}
