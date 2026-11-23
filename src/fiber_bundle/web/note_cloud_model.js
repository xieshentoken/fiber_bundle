// The v2 contract separates current entities, historical targets and saved ownership.
const kinds = new Set(["note", "moc", "expert", "unresolved"]);
const syncStates = new Set(["indexed_present", "not_synced", "unknown"]);
const record = (value) =>
  !!value && typeof value === "object" && !Array.isArray(value);
export function validCloudNode(node) {
  if (
    !record(node) ||
    typeof node.id !== "string" ||
    !node.id ||
    typeof node.label !== "string" ||
    typeof node.path !== "string" ||
    !kinds.has(node.kind) ||
    !Array.isArray(node.aliases) ||
    !node.aliases.every((id) => typeof id === "string") ||
    !["current", "missing"].includes(node.availability) ||
    typeof node.historical !== "boolean" ||
    typeof node.metadata_visible !== "boolean" ||
    !(node.entity_id === null || typeof node.entity_id === "string") ||
    !(node.note_id === null || typeof node.note_id === "string") ||
    !(node.sha256 === null || typeof node.sha256 === "string")
  )
    return false;
  if (node.kind === "unresolved")
    return (
      node.id.startsWith("missing:") &&
      node.availability === "missing" &&
      node.note_id === null &&
      node.sha256 === null &&
      [
        "identity_not_indexed",
        "publication_missing_confirmed",
        "target_unavailable",
      ].includes(node.reason) &&
      (node.reason !== "publication_missing_confirmed" ||
        (node.metadata_visible && node.historical)) &&
      (node.metadata_visible || (!node.path && !node.entity_id))
    );
  if (
    node.availability !== "current" ||
    node.historical ||
    !node.metadata_visible
  )
    return false;
  if (node.kind === "expert")
    return (
      node.id.startsWith("expert:") &&
      typeof node.version === "string" &&
      ["directory", "markdown"].includes(node.entry_kind) &&
      typeof node.artifact_stage === "string" &&
      typeof node.review_state === "string" &&
      typeof node.knowledge_verified === "boolean"
    );
  return true;
}
function validMembership(value) {
  return (
    record(value) &&
    Array.isArray(value.owners) &&
    value.owners.length > 0 &&
    value.owners.every((owner) => ["manual", "system"].includes(owner)) &&
    new Set(value.owners).size === value.owners.length &&
    typeof value.manual_locked === "boolean" &&
    syncStates.has(value.file_sync) &&
    typeof value.evidence === "string"
  );
}
function validCoverage(value) {
  return (
    record(value) &&
    typeof value.complete === "boolean" &&
    [
      "current_notes",
      "current_experts",
      "current_entities",
      "unresolved_targets",
      "membership_edges",
      "manual_memberships",
      "system_memberships",
      "manual_locked_memberships",
      "sync_indexed_present",
      "sync_not_synced",
      "sync_unknown",
    ].every((key) => Number.isSafeInteger(value[key]) && value[key] >= 0) &&
    typeof value.has_unresolved === "boolean" &&
    typeof value.targets_resolved === "boolean"
  );
}
export function noteCloudSnapshot(value, scope) {
  if (
    value?.schema !== "fiber-note-cloud/v2" ||
    value.client_id !== scope.client_id ||
    value.vault_id !== scope.vault_id ||
    !Array.isArray(value.nodes) ||
    !Array.isArray(value.edges) ||
    typeof value.snapshot_token !== "string" ||
    !validCoverage(value.coverage)
  )
    throw new Error("Invalid note cloud scope or snapshot");
  const nodes = new Map();
  for (const node of value.nodes) {
    if (!validCloudNode(node) || nodes.has(node.id))
      throw new Error("Invalid note cloud node");
    nodes.set(node.id, node);
  }
  const aliases = new Map([...nodes.keys()].map((id) => [id, id]));
  for (const node of nodes.values())
    for (const alias of node.aliases) {
      if (aliases.has(alias) && aliases.get(alias) !== node.id)
        throw new Error("Duplicate note cloud alias");
      aliases.set(alias, node.id);
    }
  const adjacency = new Map([...nodes.keys()].map((id) => [id, new Map()]));
  const edgeIds = new Set();
  for (const edge of value.edges) {
    if (
      !nodes.has(edge.source) ||
      !nodes.has(edge.target) ||
      typeof edge.id !== "string" ||
      edgeIds.has(edge.id) ||
      !["explicit", "moc"].includes(edge.kind) ||
      (edge.kind === "moc"
        ? !validMembership(edge.membership) ||
          nodes.get(edge.source).kind !== "moc"
        : edge.membership != null ||
          nodes.get(edge.source).kind === "unresolved" ||
          nodes.get(edge.target).kind === "unresolved")
    )
      throw new Error("Invalid note cloud edge");
    edgeIds.add(edge.id);
    for (const [source, target] of [
      [edge.source, edge.target],
      [edge.target, edge.source],
    ]) {
      const neighbors = adjacency.get(source);
      if (!neighbors.has(target)) neighbors.set(target, []);
      neighbors.get(target).push(edge);
    }
  }
  return { ...value, byId: nodes, adjacency };
}

export function noteCloudMatches(data, query = "") {
  const text = query.trim().toLocaleLowerCase();
  return data.nodes.filter(
    (node) =>
      !text ||
      (node.label + "\n" + node.path).toLocaleLowerCase().includes(text),
  );
}

export function noteCloudSearch(value, scope) {
  if (
    value?.schema !== "fiber-note-cloud/v2" ||
    value?.client_id !== scope.client_id ||
    value?.vault_id !== scope.vault_id ||
    typeof value.snapshot_token !== "string" ||
    !Array.isArray(value.items) ||
    !value.items.every(validCloudNode)
  )
    throw new Error("Invalid note cloud search scope");
  return value;
}
