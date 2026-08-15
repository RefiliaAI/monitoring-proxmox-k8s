import asyncio
import logging
from datetime import datetime, timezone

from app.cache import cache
from app.clients.kubernetes import K8sClient
from app.clients.proxmox import ProxmoxClient
from app.config import settings
from app.models import PodStat, ServerStats, VMStat
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


async def poll_once(px: ProxmoxClient, k8s: K8sClient) -> None:
    (server, vm_stats, raw_vms), (pod_stats, k8s_nodes, raw_pods, services) = await asyncio.gather(
        _poll_proxmox(px), _poll_kubernetes(k8s)
    )

    server_stale = server is None
    if server is None and cache.server is not None:
        server = cache.server.model_copy(update={"stale": True})

    vms_stale = not vm_stats
    vms = vm_stats if vm_stats else [v.model_copy(update={"stale": True}) for v in cache.vms]

    pods_stale = not pod_stats
    pods = pod_stats if pod_stats else [p.model_copy(update={"stale": True}) for p in cache.pods]

    topology = None
    try:
        topology = build_topology(
            gateway_ip=settings.lan_gateway_ip,
            gateway_label=settings.lan_gateway_label,
            proxmox_host_label=px.node,
            proxmox_host_ip=None,
            vms=raw_vms,
            k8s_nodes=k8s_nodes,
            pods=raw_pods,
            services=services,
        )
    except Exception:
        logger.exception("Topology build failed")

    await cache.update(server=server, vms=vms, pods=pods, topology=topology)

    if server_stale or vms_stale or pods_stale:
        logger.warning(
            "Partial poll cycle: server_stale=%s vms_stale=%s pods_stale=%s",
            server_stale,
            vms_stale,
            pods_stale,
        )


async def poller_loop(px: ProxmoxClient, k8s: K8sClient) -> None:
    while True:
        try:
            await poll_once(px, k8s)
        except Exception:
            logger.exception("Unhandled poll cycle error")
        await asyncio.sleep(settings.refresh_interval_seconds)
