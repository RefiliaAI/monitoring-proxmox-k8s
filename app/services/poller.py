import asyncio
import logging
from datetime import datetime, timezone

from app.cache import cache
from app.clients.fritzbox import FritzBoxClient
from app.clients.kubernetes import K8sClient
from app.clients.proxmox import ProxmoxClient
from app.config import settings
from app.models import ClientDevice, PodStat, ServerStats, VMStat
from app.services.client_activity import is_recently_active
from app.services.topology import build_topology

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _poll_proxmox(px: ProxmoxClient) -> tuple[ServerStats | None, list[VMStat], list[dict]]:
    now = _now()
    try:
        node_status = await px.get_node_status()
        cpu_fraction = node_status.get("cpu", 0.0)
        cpu_cores = node_status.get("cpuinfo", {}).get("cpus", 0)
        mem = node_status.get("memory", {})
        server = ServerStats(
            node_name=px.node,
            cpu_cores=cpu_cores,
            cpu_used_percent=round(cpu_fraction * 100, 1),
            mem_total_bytes=mem.get("total", 0),
            mem_used_bytes=mem.get("used", 0),
            mem_used_percent=round((mem.get("used", 0) / mem.get("total", 1)) * 100, 1),
            updated_at=now,
        )
    except Exception:
        logger.exception("Proxmox node status poll failed")
        server = None

    vm_stats: list[VMStat] = []
    raw_vms: list[dict] = []
    try:
        raw_vms = await px.fetch_all_vm_stats()
        for vm in raw_vms:
            maxmem = vm.get("maxmem", 0) or 1
            vm_stats.append(
                VMStat(
                    vmid=vm["vmid"],
                    name=vm["name"],
                    status=vm["status"],
                    cpu_percent=round(vm.get("cpu", 0.0) * 100, 1),
                    mem_used_bytes=vm.get("mem", 0),
                    mem_total_bytes=vm.get("maxmem", 0),
                    ip_addresses=vm.get("ip_addresses", []),
                    agent_reachable=vm.get("agent_reachable", False),
                    updated_at=now,
                )
            )
    except Exception:
        logger.exception("Proxmox VM stats poll failed")

    return server, vm_stats, raw_vms


async def _poll_kubernetes(
    k8s: K8sClient,
) -> tuple[list[PodStat], list[dict], list[dict], list[dict]]:
    now = _now()
    namespaces = settings.watched_namespaces_list
    k8s_nodes: list[dict] = []
    raw_pods: list[dict] = []
    services: list[dict] = []
    pod_stats: list[PodStat] = []
    try:
        k8s_nodes = k8s.list_nodes()
        raw_pods = k8s.list_pods(namespaces)
        services = k8s.list_services()
        pod_metrics = k8s.get_pod_metrics(namespaces)
        for pod in raw_pods:
            key = (pod["namespace"], pod["name"])
            metrics = pod_metrics.get(key, {})
            pod_stats.append(
                PodStat(
                    namespace=pod["namespace"],
                    name=pod["name"],
                    display_name=pod.get("display_name", pod["name"]),
                    node=pod.get("node"),
                    cpu_millicores=metrics.get("cpu_millicores"),
                    mem_bytes=metrics.get("mem_bytes"),
                    cpu_limit_millicores=pod.get("cpu_limit_millicores"),
                    mem_limit_bytes=pod.get("mem_limit_bytes"),
                    pod_ip=pod.get("pod_ip"),
                    status=pod.get("status", "Unknown"),
                    updated_at=now,
                )
            )
    except Exception:
        logger.exception("Kubernetes poll failed")

    return pod_stats, k8s_nodes, raw_pods, services


async def _poll_clients(fritz: FritzBoxClient | None) -> list[ClientDevice]:
    if fritz is None:
        return []
    now = _now()
    try:
        # fritzconnection is a blocking/sync library -- run it off the
        # event loop so a slow router response doesn't stall API requests.
        raw_clients = await asyncio.to_thread(fritz.get_client_devices)
    except Exception:
        logger.exception("FRITZ!Box client discovery failed")
        return []

    # The router only reports current on/off state -- carry forward the
    # last time each device was seen active from the previous cycle so
    # "seen in the last 24h" is something we can actually answer.
    prev_last_active = {(c.mac or c.ip): c.last_active_at for c in cache.clients}

    return [
        ClientDevice(
            ip=c["ip"],
            name=c["name"],
            mac=c["mac"],
            active=c["active"],
            interface_type=c["interface_type"],
            last_active_at=now if c["active"] else prev_last_active.get(c["mac"] or c["ip"]),
            updated_at=now,
        )
        for c in raw_clients
    ]


async def poll_once(px: ProxmoxClient, k8s: K8sClient, fritz: FritzBoxClient | None = None) -> None:
    (server, vm_stats, raw_vms), (pod_stats, k8s_nodes, raw_pods, services), client_stats = (
        await asyncio.gather(_poll_proxmox(px), _poll_kubernetes(k8s), _poll_clients(fritz))
    )

    server_stale = server is None
    if server is None and cache.server is not None:
        server = cache.server.model_copy(update={"stale": True})

    vms_stale = not vm_stats
    vms = vm_stats if vm_stats else [v.model_copy(update={"stale": True}) for v in cache.vms]

    pods_stale = not pod_stats
    pods = pod_stats if pod_stats else [p.model_copy(update={"stale": True}) for p in cache.pods]

    # Only fall back to a stale cache when discovery is actually enabled --
    # an empty list from a disabled feature is correct, not stale.
    clients_stale = settings.fritzbox_enabled and not client_stats
    clients = (
        client_stats
        if client_stats or not settings.fritzbox_enabled
        else [c.model_copy(update={"stale": True}) for c in cache.clients]
    )

    # Topology only shows recently-seen clients, matching the default
    # /api/clients view -- a device unseen for weeks would otherwise
    # clutter the diagram forever.
    recent_clients = [
        c.model_dump()
        for c in client_stats
        if is_recently_active(c.active, c.last_active_at, settings.client_recent_hours)
    ]

    topology = None
    try:
        topology = build_topology(
            gateway_ip=settings.lan_gateway_ip,
            gateway_label=settings.lan_gateway_label,
            proxmox_host_label=px.node,
            proxmox_host_ip=px.host,
            vms=raw_vms,
            k8s_nodes=k8s_nodes,
            pods=raw_pods,
            services=services,
            clients=recent_clients,
        )
    except Exception:
        logger.exception("Topology build failed")

    await cache.update(server=server, vms=vms, pods=pods, clients=clients, topology=topology)

    if server_stale or vms_stale or pods_stale or clients_stale:
        logger.warning(
            "Partial poll cycle: server_stale=%s vms_stale=%s pods_stale=%s clients_stale=%s",
            server_stale,
            vms_stale,
            pods_stale,
            clients_stale,
        )


async def poller_loop(px: ProxmoxClient, k8s: K8sClient, fritz: FritzBoxClient | None = None) -> None:
    while True:
        try:
            await poll_once(px, k8s, fritz)
        except Exception:
            logger.exception("Unhandled poll cycle error")
        await asyncio.sleep(settings.refresh_interval_seconds)
