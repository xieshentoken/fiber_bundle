// Page refresh can preserve view-owned drafts; leaving still uses the full guard.
export function pageRefreshAction(guard, refresh) {
  return () => {
    if (guard.confirmRefresh()) return refresh();
  };
}
