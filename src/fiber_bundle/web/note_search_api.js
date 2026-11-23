// Note lookup uses the admin session and a client identity, never a client token.
const record = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function results(value) {
  return (
    record(value) &&
    ["evidence_found", "no_evidence"].includes(value.status) &&
    Array.isArray(value.searched_vault_ids) &&
    value.searched_vault_ids.length <= 20 &&
    value.searched_vault_ids.every((id) => typeof id === "string") &&
    Array.isArray(value.results) &&
    value.results.length <= 20 &&
    value.results.every(
      (hit) =>
        record(hit) &&
        typeof hit.vault_id === "string" &&
        value.searched_vault_ids.includes(hit.vault_id) &&
        typeof hit.note_path === "string" &&
        typeof hit.title === "string" &&
        typeof hit.snippet === "string" &&
        Number.isInteger(hit.line) &&
        hit.line >= 1,
    )
  );
}

export function createNoteSearchApi(request) {
  return {
    searchNotes: (client_id, query, limit = 20, options = {}) =>
      request(
        "/admin/note-search",
        "POST",
        { client_id, query, limit },
        results,
        options,
      ),
  };
}
