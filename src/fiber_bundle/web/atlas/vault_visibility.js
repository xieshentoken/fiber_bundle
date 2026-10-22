// Removed registrations remain in historical ledgers, never in current GUI choices.
export function visibleVault(vault) {
  return !["detached", "trashed"].includes(
    vault.attachment_state || vault.status,
  );
}
