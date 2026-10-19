# Reuse the already qualified Pi 0.87.1 dependency image without installing packages.
# Docker builds resolve the local tag; qualification must check the base's exact
# sha256:f4d9f5f11635ad96fc7cdc9de76cbb210bf98c108cf1c80d8deb81cf1bf52a7f layers.
# Execute the resulting candidate by immutable image ID, never by this build tag.
FROM fiber-pi-m3:0.87.1
COPY --chown=1000:1000 bridge.mjs draft_tool.mjs /opt/fiber/creation/
ENTRYPOINT ["node", "/opt/fiber/creation/bridge.mjs"]
