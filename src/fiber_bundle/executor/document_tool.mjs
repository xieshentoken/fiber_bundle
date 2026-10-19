export default function (pi) {
  pi.registerTool({
    name: "fiber_extract_document",
    label: "Extract bound document",
    description:
      "Extract the authorized host-bound document candidate. Returns only status and counts.",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    async execute() {
      const response = await fetch(
        process.env.FIBER_BRIDGE + "/backend/document-extract",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
      if (!response.ok) throw new Error("Host rejected document extraction");
      return {
        content: [
          { type: "text", text: JSON.stringify(await response.json()) },
        ],
        details: {},
      };
    },
  });
}
