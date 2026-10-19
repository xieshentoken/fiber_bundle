export default function (pi) {
  pi.registerTool({
    name: "fiber_vaults",
    label: "Authorized vaults",
    description:
      "List the vault bound to this task after host-side authorization.",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    async execute() {
      const response = await fetch(
        process.env.FIBER_BRIDGE + "/backend/vaults",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
      if (!response.ok) throw new Error("Host rejected vault access");
      return {
        content: [
          { type: "text", text: JSON.stringify(await response.json()) },
        ],
        details: {},
      };
    },
  });
}
