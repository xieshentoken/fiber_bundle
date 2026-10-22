// One decorative outline family; fixed paths only, no data or arbitrary SVG markup.
const paths = {
  home: ["M3 11l9-8 9 8M5 10v11h14V10M10 21v-7h4v7"],
  imports: ["M4 4h16v16H4zM12 7v10M8 13l4 4 4-4"],
  worklist: ["M5 4h14v17H5zM8 9h8M8 13h8M8 17h5"],
  suppliers: ["M3 7h18v13H3z", "M6 7V4h12v3M8 12h8"],
  vaults: ["M3 7h18v13H3z", "M6 7V4h12v3M8 12h8"],
  clients: ["M12 3l8 4v5c0 5-8 9-8 9s-8-4-8-9V7z", "M9 12l2 2 4-4"],
  pending: ["M5 4h14v17H5z", "M9 4V2h6v2M8 13l3 3 5-6"],
  batches: ["M3 5h18v14H3z", "M3 12h5l2 3h4l2-3h5"],
  documents: ["M6 3h8l4 4v14H6z", "M14 3v5h4M9 12h6M9 16h6"],
  media: ["M3 5h18v14H3z", "M10 9l5 3-5 3z"],
  models: ["M4 4h10v10H4zM10 10h10v10H10z"],
  channels: ["M3 4h18v13H9l-5 4v-4H3z", "M7 8h10M7 12h6"],
  graph: ["M5 5h4v4H5zM15 15h4v4h-4zM15 3h4v4h-4z", "M9 7h6M7 9v8h8"],
  inventory: ["M4 4h16v16H4z", "M8 8h8M8 12h8M8 16h5"],
  creation: ["M5 3h9l5 5v13H5z", "M9 14h6M12 11v6"],
  execution: ["M5 5h4v14H5zM11 10h4v9h-4zM17 3h4v16h-4z"],
  settings: ["M4 7h16M4 17h16M8 4v6M16 14v6"],
  backend: ["M4 5h16v6H4zM4 15h16v6H4z", "M8 8h1M8 18h1"],
  backups: ["M4 4h16v4H4zM6 8v13h12V8", "M10 12h4"],
  refresh: [
    "M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 2M19 16a8 8 0 0 1-13 3l-2-2",
  ],
  search: ["M10 3a7 7 0 1 0 0 14a7 7 0 0 0 0-14M15 15l6 6"],
  chevron: ["M9 5l7 7-7 7"],
  alert: ["M12 3L2 21h20L12 3zM12 9v5M12 17h.01"],
  batch: ["M3 5h18v14H3zM3 12h5l2 3h4l2-3h5"],
  document: ["M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6"],
  import: ["M4 4h16v16H4zM12 7v10M8 13l4 4 4-4"],
  expert: ["M4 4h16v16H4zM8 8h8M8 12h8M8 16h5"],
  automatic: ["M12 3l-7 10h6l-1 8 9-12h-6l-1-6"],
  articles: ["M4 4h16v16H4zM8 8h8M8 12h8M8 16h5"],
  channel: ["M3 4h18v13H9l-5 4v-4H3zM7 8h10M7 12h6"],
};
export function icon(name) {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.7",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
    focusable: "false",
    class: "icon",
  }))
    svg.setAttribute(key, value);
  for (const shape of paths[name] || []) {
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d", shape);
    svg.append(path);
  }
  return svg;
}
