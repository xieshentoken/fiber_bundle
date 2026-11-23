import { createNoteCloudView } from "../note_cloud.js";
export function createView(ctx) {
  return createNoteCloudView(ctx.api);
}
