// Negative layers sit above the gateway, positive below -- keeps a clear
// "clients above, server stack below" read: LAN devices are consumers of
// the network, everything else is infrastructure the router hosts.
const TOPO_LAYER_BY_TYPE = {
  client: -1,
  gateway: 0,
  proxmox_host: 1,
  vm: 2,
  k8s_node: 3,
  namespace: 4,
  k8s_pod: 5,
  k8s_service: 6,
};

// Layout tuning: each layer wraps its nodes into multiple rows instead of
// squeezing them all onto one line, so the diagram stays legible as more
// k3s microservices are added instead of growing overlapping labels.
const TOPO_CANVAS_WIDTH = 1400;
const TOPO_SLOT_WIDTH = 168;
const TOPO_ROW_HEIGHT = 96;
const TOPO_LAYER_GAP = 40;
const TOPO_TOP_MARGIN = 50;
const TOPO_BOTTOM_MARGIN = 30;
const TOPO_MAX_LABEL_CHARS = 16;

function truncateLabel(label) {
  if (!label || label.length <= TOPO_MAX_LABEL_CHARS) return label;
  return `${label.slice(0, TOPO_MAX_LABEL_CHARS - 1)}…`;
}

const TOPO_TYPE_META = {
  gateway: { label: "Gateway / router", colorVar: "--cat-gateway", icon: "\u{1F4E1}" },
  proxmox_host: { label: "Proxmox host", colorVar: "--cat-proxmox", icon: "\u{1F5A5}" },
  client: { label: "LAN device", colorVar: "--cat-client", icon: "\u{1F4F1}" },
  vm: { label: "Virtual machine", colorVar: "--cat-vm", icon: "\u{1F4BB}" },
  k8s_node: { label: "k3s node", colorVar: "--cat-k8s-node", icon: "☸" },
  // Muted/neutral rather than a bright categorical hue on purpose --
  // a namespace is a structural grouping, not a peer entity next to a
  // VM or pod, so it shouldn't visually compete with the real ones.
  namespace: { label: "Namespace", colorVar: "--text-muted", icon: "\u{1F4C1}" },
  k8s_pod: { label: "Microservice (pod)", colorVar: "--cat-k8s-pod", icon: "\u{1F4E6}" },
  k8s_service: { label: "k8s service", colorVar: "--cat-k8s-service", icon: "\u{1F517}" },
};

const TOPO_STATUS_COLOR_VAR = {
  ok: "--status-ok",
  warn: "--status-warning",
  critical: "--status-critical",
  unknown: "--status-unknown",
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Which namespaces are collapsed (their pods/services hidden), persisted
// across reloads. Keyed by the raw namespace name -- the same value
// k8s_pod/k8s_service nodes carry in meta.namespace.
const TOPO_COLLAPSED_STORAGE_KEY = "topology-collapsed-namespaces";

function getCollapsedNamespaces() {
  try {
    const raw = localStorage.getItem(TOPO_COLLAPSED_STORAGE_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();
  }
}

function setCollapsedNamespaces(set) {
  try {
    localStorage.setItem(TOPO_COLLAPSED_STORAGE_KEY, JSON.stringify([...set]));
  } catch {
    // localStorage unavailable (private browsing, quota, ...) -- the
    // toggle still works for this session, it just won't persist.
  }
}

// Re-render target for toggling a namespace without waiting for the next
// poll cycle -- set by renderTopology on every call.
let lastRenderedTopology = null;

function toggleNamespaceCollapsed(svgEl, namespace) {
  const collapsed = getCollapsedNamespaces();
  if (collapsed.has(namespace)) {
    collapsed.delete(namespace);
  } else {
    collapsed.add(namespace);
  }
  setCollapsedNamespaces(collapsed);
  if (lastRenderedTopology) renderTopology(svgEl, lastRenderedTopology);
}

function renderTopology(svgEl, topology) {
  lastRenderedTopology = topology;
  const collapsed = getCollapsedNamespaces();

  // Count each namespace's children *before* filtering, so a collapsed
  // namespace can still show "how much is hidden".
  const namespaceChildCounts = {};
  for (const n of topology.nodes) {
    if ((n.type === "k8s_pod" || n.type === "k8s_service") && n.meta && n.meta.namespace) {
      namespaceChildCounts[n.meta.namespace] = (namespaceChildCounts[n.meta.namespace] || 0) + 1;
    }
  }

  const nodes = topology.nodes.filter((n) => {
    if (n.type !== "k8s_pod" && n.type !== "k8s_service") return true;
    return !(n.meta && collapsed.has(n.meta.namespace));
  });
  const { edges } = topology;

  const layers = {};
  for (const node of nodes) {
    const layer = TOPO_LAYER_BY_TYPE[node.type] ?? 0;
    (layers[layer] = layers[layer] || []).push(node);
  }
  const layerKeys = Object.keys(layers).map(Number).sort((a, b) => a - b);

  const nodeRadius = 20;
  const width = TOPO_CANVAS_WIDTH;
  const columns = Math.max(1, Math.floor(width / TOPO_SLOT_WIDTH));

  const positions = new Map();
  let cursorY = TOPO_TOP_MARGIN;
  for (const layerIdx of layerKeys) {
    const nodesInLayer = layers[layerIdx];
    const rowCount = Math.ceil(nodesInLayer.length / columns);
    nodesInLayer.forEach((node, i) => {
      const row = Math.floor(i / columns);
      const rowStart = row * columns;
      const itemsInRow = Math.min(columns, nodesInLayer.length - rowStart);
      const col = i - rowStart;
      const slotWidth = width / itemsInRow;
      positions.set(node.id, {
        x: slotWidth * (col + 0.5),
        y: cursorY + row * TOPO_ROW_HEIGHT + TOPO_ROW_HEIGHT / 2,
      });
    });
    cursorY += rowCount * TOPO_ROW_HEIGHT + TOPO_LAYER_GAP;
  }
  const height = cursorY - TOPO_LAYER_GAP + TOPO_BOTTOM_MARGIN;

  svgEl.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svgEl.innerHTML = "";

  const edgeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  for (const edge of edges) {
    const from = positions.get(edge.source);
    const to = positions.get(edge.target);
    if (!from || !to) continue;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const midY = (from.y + to.y) / 2;
    path.setAttribute(
      "d",
      `M ${from.x} ${from.y} C ${from.x} ${midY}, ${to.x} ${midY}, ${to.x} ${to.y}`
    );
    path.setAttribute("class", "topo-edge");
    edgeGroup.appendChild(path);
  }
  svgEl.appendChild(edgeGroup);

  const nodeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  for (const node of nodes) {
    const pos = positions.get(node.id);
    if (!pos) continue;
    const meta = TOPO_TYPE_META[node.type] || TOPO_TYPE_META.vm;
    const fillColor = cssVar(meta.colorVar);
    const ringColor = cssVar(TOPO_STATUS_COLOR_VAR[node.status] || TOPO_STATUS_COLOR_VAR.unknown);
    const isNamespace = node.type === "namespace";
    const isNamespaceCollapsed = isNamespace && collapsed.has(node.label);

    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);
    if (isNamespace) {
      g.setAttribute("class", "topo-node-clickable");
      g.addEventListener("click", () => toggleNamespaceCollapsed(svgEl, node.label));
    }

    const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    ring.setAttribute("r", nodeRadius + 4);
    ring.setAttribute("class", "topo-node-ring");
    ring.setAttribute("stroke", ringColor);
    if (isNamespaceCollapsed) ring.setAttribute("stroke-dasharray", "4 3");
    g.appendChild(ring);

    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("r", nodeRadius);
    circle.setAttribute("fill", fillColor);
    g.appendChild(circle);

    const icon = document.createElementNS("http://www.w3.org/2000/svg", "text");
    icon.setAttribute("text-anchor", "middle");
    icon.setAttribute("dominant-baseline", "central");
    icon.setAttribute("font-size", "18");
    // Closed vs. open folder doubles as the collapsed/expanded indicator.
    icon.textContent = isNamespace ? (isNamespaceCollapsed ? "\u{1F4C1}" : "\u{1F4C2}") : meta.icon;
    g.appendChild(icon);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("y", nodeRadius + 18);
    label.setAttribute("class", "topo-node-label");
    const hiddenCount = isNamespaceCollapsed ? namespaceChildCounts[node.label] || 0 : 0;
    label.textContent = hiddenCount
      ? `${truncateLabel(node.label)} (${hiddenCount} hidden)`
      : truncateLabel(node.label);
    g.appendChild(label);

    if (node.ip) {
      const sub = document.createElementNS("http://www.w3.org/2000/svg", "text");
      sub.setAttribute("text-anchor", "middle");
      sub.setAttribute("y", nodeRadius + 32);
      sub.setAttribute("class", "topo-node-sub");
      sub.textContent = node.ip;
      g.appendChild(sub);
    }

    const fullName = node.meta && node.meta.full_name;
    const titleEl = document.createElementNS("http://www.w3.org/2000/svg", "title");
    titleEl.textContent = isNamespace
      ? `${node.label} (namespace) — click to ${isNamespaceCollapsed ? "expand" : "collapse"}`
      : `${fullName || node.label} (${meta.label}) — ${node.ip || "no IP"} — ${node.status}`;
    g.appendChild(titleEl);

    nodeGroup.appendChild(g);
  }
  svgEl.appendChild(nodeGroup);
}

function renderTopologyLegend(container) {
  container.innerHTML = "";
  for (const [type, meta] of Object.entries(TOPO_TYPE_META)) {
    if (container.querySelector(`[data-legend-type="${type}"]`)) continue;
    const item = document.createElement("div");
    item.className = "legend-item";
    item.dataset.legendType = type;
    item.innerHTML = `<span class="legend-swatch" style="background:${cssVar(meta.colorVar)}"></span>${meta.label}`;
    container.appendChild(item);
  }
}

function renderTopologyTable(tbody, topology) {
  tbody.innerHTML = "";
  // Mirror the diagram's collapsed namespaces here too, since this table
  // is meant to be the same information in an accessible form, not a
  // separate view.
  const collapsed = getCollapsedNamespaces();
  for (const node of topology.nodes) {
    if ((node.type === "k8s_pod" || node.type === "k8s_service") && node.meta && collapsed.has(node.meta.namespace)) {
      continue;
    }
    const meta = TOPO_TYPE_META[node.type] || {};
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${node.label}</td>
      <td>${meta.label || node.type}</td>
      <td>${node.ip || "–"}</td>
      <td>${node.status}</td>
    `;
    tbody.appendChild(tr);
  }
}
