function formatBytes(bytes) {
  if (bytes == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  const digits = value >= 100 || i === 0 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[i]}`;
}

function formatMillicores(mc) {
  if (mc == null) return "–";
  if (mc >= 1000) return `${(mc / 1000).toFixed(2)} cores`;
  return `${mc} mCPU`;
}

// Sequential ramp step for a magnitude percent (0-100). Light -> dark = low -> high,
// per the dataviz color-formula: magnitude gets one hue, never health colors.
function seqStepForPercent(pct) {
  if (pct == null) return "var(--seq-300)";
  if (pct < 20) return "var(--seq-250)";
  if (pct < 40) return "var(--seq-350)";
  if (pct < 60) return "var(--seq-400)";
  if (pct < 80) return "var(--seq-500)";
  return "var(--seq-600)";
}

function clampPercent(pct) {
  if (pct == null || Number.isNaN(pct)) return 0;
  return Math.max(0, Math.min(100, pct));
}

// Renders a labelled meter (Tier-1 "meter / progress track" component).
// `valueText` is the human string shown at the right of the label row.
function renderMeter({ label, pct, valueText, large = false }) {
  const safePct = clampPercent(pct);
  const fillColor = seqStepForPercent(pct);
  const wrap = document.createElement("div");
  wrap.className = "meter";
  wrap.innerHTML = `
    <div class="meter-label-row">
      <span>${label}</span>
      <span class="meter-value">${valueText}</span>
    </div>
    <div class="meter-track" role="progressbar" aria-valuenow="${safePct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100" aria-label="${label}">
      <div class="meter-fill" style="width:${safePct}%;background:${fillColor}"></div>
    </div>
  `;
  return wrap;
}

const STATUS_LABELS = {
  ok: "Healthy",
  warning: "Degraded",
  serious: "At risk",
  critical: "Critical",
  unknown: "Unknown",
};

function renderStatusBadge(statusKey, textOverride) {
  const key = ["ok", "warning", "serious", "critical", "unknown"].includes(statusKey)
    ? statusKey
    : "unknown";
  const span = document.createElement("span");
  span.className = `status-badge status-${key}`;
  span.innerHTML = `<span class="status-dot"></span>${textOverride || STATUS_LABELS[key]}`;
  return span;
}

function renderStatTile(value, label) {
  const div = document.createElement("div");
  div.className = "stat-tile";
  div.innerHTML = `
    <div class="stat-tile-value">${value}</div>
    <div class="stat-tile-label">${label}</div>
  `;
  return div;
}
