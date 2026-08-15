function vmStatusKey(vm) {
  if (vm.status !== "running") return "unknown";
  if (!vm.agent_reachable) return "warning";
  return "ok";
}

function vmStatusText(vm) {
  if (vm.status !== "running") return vm.status.charAt(0).toUpperCase() + vm.status.slice(1);
  if (!vm.agent_reachable) return "Agent unreachable";
  return "Running";
}

function podStatusKey(pod) {
  switch (pod.status) {
    case "Running":
    case "Succeeded":
      return "ok";
    case "Pending":
      return "warning";
    case "Failed":
      return "critical";
    default:
      return "unknown";
  }
}

function renderOverview(server) {
  const hero = document.getElementById("overview-hero");
  hero.innerHTML = "";

  const cpuCard = document.createElement("div");
  cpuCard.className = "card hero-card";
  cpuCard.appendChild(
    renderMeter({
      label: "CPU usage",
      pct: server.cpu_used_percent,
      valueText: `${server.cpu_used_percent.toFixed(1)}%`,
    })
  );
  const cpuStats = document.createElement("div");
  cpuStats.className = "hero-stat-row";
  cpuStats.appendChild(renderStatTile(server.cpu_cores, "CPU cores"));
  cpuStats.appendChild(renderStatTile(`${server.cpu_used_percent.toFixed(0)}%`, "Utilized"));
  cpuCard.appendChild(cpuStats);
  hero.appendChild(cpuCard);

  const memCard = document.createElement("div");
  memCard.className = "card hero-card";
  memCard.appendChild(
    renderMeter({
      label: "RAM usage",
      pct: server.mem_used_percent,
      valueText: `${server.mem_used_percent.toFixed(1)}%`,
    })
  );
  const memStats = document.createElement("div");
  memStats.className = "hero-stat-row";
  memStats.appendChild(renderStatTile(formatBytes(server.mem_used_bytes), "Used"));
  memStats.appendChild(renderStatTile(formatBytes(server.mem_total_bytes), "Total"));
  memCard.appendChild(memStats);
  hero.appendChild(memCard);

  if (server.stale) {
    const warn = document.createElement("div");
    warn.className = "empty-state";
    warn.textContent = "Showing last-known values — Proxmox is not responding right now.";
    hero.appendChild(warn);
  }
}

function renderVMs(vms) {
  const grid = document.getElementById("vm-grid");
  grid.innerHTML = "";
  if (!vms.length) {
    grid.innerHTML = '<div class="empty-state">No VMs reported yet.</div>';
    return;
  }
  for (const vm of vms) {
    const card = document.createElement("div");
    card.className = "card entity-card";

    const head = document.createElement("div");
    head.className = "entity-head";
    head.innerHTML = `
      <div>
        <div class="entity-name">${vm.name}</div>
        <div class="entity-sub">VMID ${vm.vmid}</div>
      </div>
    `;
    head.appendChild(renderStatusBadge(vmStatusKey(vm), vmStatusText(vm)));
    card.appendChild(head);

    card.appendChild(
      renderMeter({
        label: "CPU",
        pct: vm.cpu_percent,
        valueText: `${vm.cpu_percent.toFixed(1)}%`,
      })
    );
    const memPct = vm.mem_total_bytes ? (vm.mem_used_bytes / vm.mem_total_bytes) * 100 : 0;
    card.appendChild(
      renderMeter({
        label: "RAM",
        pct: memPct,
        valueText: `${formatBytes(vm.mem_used_bytes)} / ${formatBytes(vm.mem_total_bytes)}`,
      })
    );

    const metaRow = document.createElement("div");
    metaRow.className = "entity-meta-row";
    metaRow.innerHTML = vm.ip_addresses.length
      ? vm.ip_addresses.map((ip) => `<code>${ip}</code>`).join("")
      : "<span>No IP reported</span>";
    card.appendChild(metaRow);

    grid.appendChild(card);
  }
}

function renderPods(pods) {
  const grid = document.getElementById("pod-grid");
  grid.innerHTML = "";
  if (!pods.length) {
    grid.innerHTML = '<div class="empty-state">No microservices reported yet.</div>';
    return;
  }
  for (const pod of pods) {
    const card = document.createElement("div");
    card.className = "card entity-card";

    const head = document.createElement("div");
    head.className = "entity-head";
    head.innerHTML = `
      <div>
        <div class="entity-name" title="${pod.name}">${pod.display_name}</div>
        <div class="entity-sub">${pod.namespace} · ${pod.node || "unscheduled"}</div>
      </div>
    `;
    head.appendChild(renderStatusBadge(podStatusKey(pod), pod.status));
    card.appendChild(head);

    const cpuPct = pod.cpu_limit_millicores && pod.cpu_millicores != null
      ? (pod.cpu_millicores / pod.cpu_limit_millicores) * 100
      : null;
    card.appendChild(
      renderMeter({
        label: "CPU",
        pct: cpuPct,
        valueText: formatMillicores(pod.cpu_millicores),
      })
    );

    const memPct = pod.mem_limit_bytes && pod.mem_bytes != null
      ? (pod.mem_bytes / pod.mem_limit_bytes) * 100
      : null;
    card.appendChild(
      renderMeter({
        label: "RAM",
        pct: memPct,
        valueText: formatBytes(pod.mem_bytes),
      })
    );

    const metaRow = document.createElement("div");
    metaRow.className = "entity-meta-row";
    metaRow.innerHTML = pod.pod_ip ? `<code>${pod.pod_ip}</code>` : "<span>No pod IP</span>";
    card.appendChild(metaRow);

    grid.appendChild(card);
  }
}

function renderTopologyView(topology) {
  const svg = document.getElementById("topology-svg");
  renderTopology(svg, topology);
  renderTopologyLegend(document.getElementById("topology-legend"));
  renderTopologyTable(document.querySelector("#topology-table tbody"), topology);
}

function setLastUpdated(iso) {
  const pill = document.getElementById("last-updated");
  try {
    const d = new Date(iso);
    pill.textContent = `Updated ${d.toLocaleTimeString()}`;
  } catch {
    pill.textContent = "Updated";
  }
}

function setupTabs() {
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.setAttribute("aria-selected", "false"));
      tab.setAttribute("aria-selected", "true");
      const target = tab.dataset.view;
      document.querySelectorAll(".view").forEach((view) => {
        view.hidden = view.dataset.view !== target;
      });
    });
  });
}

async function bootstrap() {
  setupTabs();

  let refreshIntervalMs = 15000;
  try {
    const meta = await fetchJSON("/api/meta");
    refreshIntervalMs = Math.max(5, meta.refresh_interval_seconds) * 1000;
  } catch (err) {
    console.error("[meta]", err);
  }

  startPolling(
    [
      async () => {
        const server = await fetchJSON("/api/server");
        renderOverview(server);
        setLastUpdated(server.updated_at);
      },
      async () => {
        const vms = await fetchJSON("/api/vms");
        renderVMs(vms);
      },
      async () => {
        const pods = await fetchJSON("/api/pods");
        renderPods(pods);
      },
      async () => {
        const topology = await fetchJSON("/api/topology");
        renderTopologyView(topology);
      },
    ],
    refreshIntervalMs
  );
}

document.addEventListener("DOMContentLoaded", bootstrap);
