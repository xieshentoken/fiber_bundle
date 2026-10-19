import { constants } from "node:fs";
import { open, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
const marker = "未核验草稿 / UNVERIFIED DRAFT\n\n";
let used = false;
async function exclusive(path, content) {
  const file = await open(
    path,
    constants.O_WRONLY |
      constants.O_CREAT |
      constants.O_EXCL |
      constants.O_NOFOLLOW,
    0o600,
  );
  try {
    await file.writeFile(content);
    await file.sync();
  } finally {
    await file.close();
  }
}
export default function (pi) {
  pi.registerTool({
    name: "fiber_write_draft",
    label: "Write unverified draft",
    description:
      "Write one unverified UTF-8 draft to the fixed task output. No paths, source retrieval, publication or revision tools are available.",
    parameters: {
      type: "object",
      properties: { draft: { type: "string", minLength: 1 } },
      required: ["draft"],
      additionalProperties: false,
    },
    async execute(_id, args) {
      if (
        used ||
        !args ||
        Object.keys(args).length !== 1 ||
        typeof args.draft !== "string" ||
        !args.draft.trim() ||
        Array.from(args.draft).some((c) => {
          const code = c.codePointAt(0);
          return (code < 32 && ![9, 10, 13].includes(code)) || code === 127;
        })
      ) {
        throw new Error("Draft rejected");
      }
      const content = Buffer.from(marker + args.draft, "utf8");
      if (
        content.length > 8 * 1024 * 1024 ||
        content.toString("utf8") !== marker + args.draft
      )
        throw new Error("Draft rejected");
      used = true; // Failure is not permission to repeat a partial write.
      const spec = JSON.parse(
        await readFile("/tmp/fiber/creation-context.json", "utf8"),
      );
      const filename = "draft." + spec.format;
      await exclusive("/task/" + filename, content);
      const receipt = {
        schema: "document-creation-output/v1",
        context: spec.context,
        filename,
        sha256: createHash("sha256").update(content).digest("hex"),
        byte_count: content.length,
      };
      await exclusive("/task/output.json", JSON.stringify(receipt));
      const directory = await open(
        "/task",
        constants.O_RDONLY | constants.O_DIRECTORY,
      );
      try {
        await directory.sync();
      } finally {
        await directory.close();
      }
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({
              status: "unverified_draft_written",
              filename,
              byte_count: content.length,
            }),
          },
        ],
        details: {},
      };
    },
  });
}
