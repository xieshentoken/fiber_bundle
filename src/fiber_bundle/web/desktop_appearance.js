// Presentation-only bridge. No credentials, filesystem access or business requests.
export function createDesktopAppearanceSync(bridge, onWarning = () => {}) {
  let last = null,
    queue = Promise.resolve();
  return (value) => {
    if (typeof bridge?.applyAppearance !== "function") return queue;
    const payload = { theme: value.theme, defaultTheme: value.defaultTheme };
    const key = JSON.stringify(payload);
    if (key === last) return queue;
    last = key;
    queue = queue.then(async () => {
      try {
        const result = await bridge.applyAppearance(payload);
        if (result?.defaultSaved !== true) throw new Error("default_not_saved");
      } catch (error) {
        // Keep this payload deduplicated even after failure: cancelling a font
        // preview on navigation is not a new request to save a desktop default.
        // A changed theme/default or a fresh page load may try again.
        if (last === key) onWarning(error);
      }
    });
    return queue;
  };
}
