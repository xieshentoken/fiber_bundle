ARG BASE=node:22.22.2-bookworm-slim@sha256:9f6d5975c7dca860947d3915877f85607946403fc55349f39b4bc3688448bb6e
FROM ${BASE}
WORKDIR /opt/fiber
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY bridge.mjs tool.mjs article_tool.mjs document_tool.mjs ./
COPY processing_args.mjs provider_bridge.mjs processing-skills.json *-processing.md ./
ENV HOME=/tmp/fiber PI_CODING_AGENT_DIR=/tmp/fiber/agent
USER 1000:1000
WORKDIR /task
ENTRYPOINT ["node", "/opt/fiber/bridge.mjs"]
