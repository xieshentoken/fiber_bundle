// Keyboard navigation never commits a native value; confirmation does that separately.
export function availableOptions(options) {
  return Array.from(options)
    .map((option, index) => ({ option, index }))
    .filter(
      ({ option }) =>
        !option.disabled &&
        !option.hidden &&
        !(
          option.parentElement?.tagName === "OPTGROUP" &&
          option.parentElement.disabled
        ),
    );
}
export function nextOption(options, current, key) {
  const enabled = availableOptions(options).map(({ index }) => index);
  if (!enabled.length) return -1;
  if (key === "Home") return enabled[0];
  if (key === "End") return enabled.at(-1);
  const position = enabled.indexOf(current);
  if (position < 0) return key === "ArrowUp" ? enabled.at(-1) : enabled[0];
  return enabled[
    (position + (key === "ArrowUp" ? -1 : 1) + enabled.length) % enabled.length
  ];
}
export function findOption(options, current, query) {
  const enabled = availableOptions(options);
  const needle = query.toLocaleLowerCase();
  const start = enabled.findIndex(({ index }) => index === current);
  for (let offset = 1; offset <= enabled.length; offset++) {
    const item = enabled[(Math.max(-1, start) + offset) % enabled.length];
    if (item.option.textContent.trim().toLocaleLowerCase().startsWith(needle))
      return item.index;
  }
  return current;
}
