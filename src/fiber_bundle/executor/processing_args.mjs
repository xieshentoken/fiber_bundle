// Fixed processing skills are bundled with the immutable executor image.
export function processingArgs(kind) {
  if (!["article", "video", "expert"].includes(kind))
    throw new Error("processing_skill_unknown");
  return [
    "--no-context-files",
    "--offline",
    "--skill",
    `/opt/fiber/${kind}-processing.md`,
    "--append-system-prompt",
    `/opt/fiber/${kind}-processing.md`,
    "--tools",
    "read,write,edit",
  ];
}
