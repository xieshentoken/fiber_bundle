// The web choice is authoritative. Missing old bridges never block navigation.
export function createDesktopLocaleSync(bridge, warn = console.warn) {
  let previous = null,
    queue = Promise.resolve();
  return (language) => {
    if (typeof bridge?.applyLocale !== "function" || previous === language)
      return queue;
    previous = language;
    queue = queue.then(async () => {
      try {
        const result = await bridge.applyLocale({ language });
        if (result?.cacheSaved !== true)
          throw new Error("locale_cache_not_saved");
      } catch {
        warn("Desktop language sync unavailable; reopen the app to retry.");
      }
    });
    return queue;
  };
}
