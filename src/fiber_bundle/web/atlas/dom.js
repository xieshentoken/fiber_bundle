// DOM construction helpers. Text always goes through textContent; no HTML strings.
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [
  ...root.querySelectorAll(selector),
];

// h("button.primary", { type: "button", onclick }, "Save", icon("check"))
export function h(spec, props, ...children) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");
  if (
    props === null ||
    props === undefined ||
    typeof props !== "object" ||
    props instanceof Node ||
    Array.isArray(props)
  ) {
    children.unshift(props);
    props = {};
  }
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className += (node.className ? " " : "") + value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function")
      node.addEventListener(key.slice(2), value);
    else if (
      key in node &&
      !key.includes("-") &&
      key !== "list" &&
      key !== "form"
    )
      node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  append(node, children);
  return node;
}
function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
}
export function clear(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

// One outline icon family drawn from fixed paths; no data reaches SVG markup.
const paths = {
  home: ["M3 11l9-8 9 8", "M5 10v10h14V10", "M10 20v-6h4v6"],
  ask: ["M4 5h16v11H9l-5 4z", "M9 9h6", "M9 12h4"],
  inbox: ["M3 13l3-8h12l3 8v6H3z", "M3 13h5l1 3h6l1-3h5"],
  vaults: ["M4 6h16v14H4z", "M7 6V3h10v3", "M9 11h6"],
  imports: ["M12 3v12", "M7 10l5 5 5-5", "M4 17v3h16v-3"],
  inventory: ["M4 4h16v16H4z", "M8 8h8", "M8 12h8", "M8 16h5"],
  graph: [
    "M6 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
    "M18 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
    "M12 18m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
    "M8 6h8",
    "M7 8l4 8",
    "M17 8l-4 8",
  ],
  models: [
    "M12 3l8 4.5v9L12 21l-8-4.5v-9z",
    "M12 12l8-4.5",
    "M12 12v9",
    "M12 12L4 7.5",
  ],
  creation: ["M5 3h9l5 5v13H5z", "M14 3v5h5", "M9 13h6", "M12 10v6"],
  clients: ["M12 3l8 4v5c0 5-8 9-8 9s-8-4-8-9V7z", "M9 12l2 2 4-4"],
  channels: ["M3 4h18v13H9l-5 4v-4H3z", "M7 9h10", "M7 13h6"],
  execution: ["M4 20V10", "M10 20V4", "M16 20v-7", "M22 20H2"],
  backend: ["M4 4h16v6H4z", "M4 14h16v6H4z", "M8 7h.01", "M8 17h.01"],
  backups: ["M4 4h16v4H4z", "M6 8v12h12V8", "M10 12h4"],
  settings: [
    "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z",
  ],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-4-4"],
  refresh: ["M20 11a8 8 0 1 0-2.3 5.7", "M20 4v7h-7"],
  logout: ["M15 4h4v16h-4", "M10 8l-4 4 4 4", "M6 12h10"],
  chevron: ["M9 6l6 6-6 6"],
  back: ["M15 6l-6 6 6 6"],
  down: ["M6 9l6 6 6-6"],
  close: ["M6 6l12 12", "M18 6L6 18"],
  plus: ["M12 5v14", "M5 12h14"],
  check: ["M5 12l5 5 9-10"],
  alert: ["M12 3l10 18H2z", "M12 10v5", "M12 18h.01"],
  info: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 11v6", "M12 7.5h.01"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7v5l3 2"],
  moon: ["M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"],
  sun: [
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
    "M12 2v2",
    "M12 20v2",
    "M4.9 4.9l1.4 1.4",
    "M17.7 17.7l1.4 1.4",
    "M2 12h2",
    "M20 12h2",
    "M4.9 19.1l1.4-1.4",
    "M17.7 6.3l1.4-1.4",
  ],
  globe: [
    "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
    "M3 12h18",
    "M12 3c3 3 3 15 0 18",
    "M12 3c-3 3-3 15 0 18",
  ],
  menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
  sidebar: ["M4 4h16v16H4z", "M9 4v16"],
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  external: ["M14 4h6v6", "M20 4l-9 9", "M18 14v6H4V6h6"],
  file: ["M6 3h8l4 4v14H6z", "M14 3v4h4"],
  document: ["M6 3h8l4 4v14H6z", "M14 3v4h4", "M9 12h6", "M9 16h6"],
  media: ["M3 5h18v14H3z", "M10 9l5 3-5 3z"],
  batch: ["M3 5h18v14H3z", "M3 12h5l2 3h4l2-3h5"],
  approval: ["M5 4h14v17H5z", "M9 4V2h6v2", "M8 13l3 3 5-6"],
  expert: ["M12 3l2.5 5 5.5.8-4 3.9.9 5.5-4.9-2.6-4.9 2.6.9-5.5-4-3.9 5.5-.8z"],
  automatic: [
    "M12 4v3",
    "M12 17v3",
    "M4 12h3",
    "M17 12h3",
    "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  ],
  article: ["M4 4h16v16H4z", "M8 8h8", "M8 12h8", "M8 16h4"],
  key: ["M8 14a4 4 0 1 1 3.5-2H21v3h-2v2h-3v-2h-4.5A4 4 0 0 1 8 14z"],
  link: [
    "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1",
    "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  ],
  play: ["M7 4l13 8-13 8z"],
  stop: ["M6 6h12v12H6z"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"],
  upload: ["M12 16V4", "M7 9l5-5 5 5", "M4 17v3h16v-3"],
  download: ["M12 4v12", "M7 11l5 5 5-5", "M4 17v3h16v-3"],
  filter: ["M4 5h16l-6 8v6l-4-2v-4z"],
  spark: [
    "M12 3v4",
    "M12 17v4",
    "M3 12h4",
    "M17 12h4",
    "M6 6l2.5 2.5",
    "M15.5 15.5L18 18",
    "M6 18l2.5-2.5",
    "M15.5 8.5L18 6",
  ],
  layers: ["M12 3l9 5-9 5-9-5z", "M3 13l9 5 9-5"],
  user: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4 21a8 8 0 0 1 16 0"],
  dot: ["M12 12h.01"],
  edit: ["M4 20h4L19 9l-4-4L4 16z", "M13 7l4 4"],
  eye: [
    "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z",
    "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  ],
  shield: ["M12 3l8 4v5c0 5-8 9-8 9s-8-4-8-9V7z"],
  swap: ["M7 4L3 8l4 4", "M3 8h14", "M17 12l4 4-4 4", "M21 16H7"],
  folder: ["M3 6h6l2 2h10v11H3z"],
  pulse: ["M3 12h4l3-7 4 14 3-7h4"],
  coin: [
    "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
    "M15 9h-4a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H9",
    "M12 6v2",
    "M12 16v2",
  ],
};
const SVG = "http://www.w3.org/2000/svg";
export function icon(name, label) {
  const svg = document.createElementNS(SVG, "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.75",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    focusable: "false",
    class: "a-icon",
  }))
    svg.setAttribute(key, value);
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else svg.setAttribute("aria-hidden", "true");
  for (const shape of paths[name] || paths.dot) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", shape);
    svg.append(path);
  }
  return svg;
}
export const iconNames = Object.keys(paths);
