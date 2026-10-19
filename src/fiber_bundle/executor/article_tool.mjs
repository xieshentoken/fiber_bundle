export default function (pi) {
  pi.registerTool({
    name: "fiber_capture_article",
    label: "Capture bound article",
    description:
      "Capture the already authorized, host-bound article. Returns only status and counts.",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    async execute() {
      const response = await fetch(
        process.env.FIBER_BRIDGE + "/backend/article-capture",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
      if (!response.ok) throw new Error("Host rejected article capture");
      return {
        content: [
          { type: "text", text: JSON.stringify(await response.json()) },
        ],
        details: {},
      };
    },
  });
}
