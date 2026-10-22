import { h } from "./dom.js";
import { createSupplierView } from "../suppliers_view.js";
export function createView(ctx) {
  const root = h("div");
  return {
    node: root,
    ...createSupplierView(ctx.api, root, { navigate: ctx.navigate }),
  };
}
