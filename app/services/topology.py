from datetime import datetime, timezone

from app.models import Topology, TopologyEdge, TopologyNode


def build_topology(
    *,
    gateway_ip: str,
    gateway_label: str,
    proxmox_host_label: str,
    proxmox_host_ip: str | None,
    vms: list[dict],
    k8s_nodes: list[dict],
    pods: list[dict],
    services: list[dict],
) -> Topology:
    """Compose the unified topology graph. Every node/edge lands in the
    same two flat lists regardless of whether it came from Proxmox,
    Kubernetes, or the one manually-configured gateway seed -- the
    frontend only needs `type` (for icon/color) and `status` (for
    health), never the provenance.
    """
    nodes: list[TopologyNode] = []
    edges: list[TopologyEdge] = []

    gateway_id = "gateway"
    nodes.append(
        TopologyNode(
            id=gateway_id,
            type="gateway",
            label=gateway_label,
            ip=gateway_ip,
            status="ok",
        )
    )

    host_id = "proxmox-host"
    nodes.append(
        TopologyNode(
            id=host_id,
            type="proxmox_host",
            label=proxmox_host_label,
            ip=proxmox_host_ip,
            status="ok",
        )
    )
    edges.append(TopologyEdge(source=gateway_id, target=host_id, type="network"))

    vm_id_by_vmid: dict[int, str] = {}
    for vm in vms:
        vmid = vm["vmid"]
        node_id = f"vm-{vmid}"
        vm_id_by_vmid[vmid] = node_id
        ip_addresses = vm.get("ip_addresses") or []
        primary_ip = ip_addresses[0] if ip_addresses else None
        status = "ok" if vm.get("status") == "running" else "warn"
        nodes.append(
            TopologyNode(
                id=node_id,
                type="vm",
                label=vm.get("name", f"vm-{vmid}"),
                ip=primary_ip,
                status=status,
                meta={"vmid": vmid, "ip_addresses": ip_addresses},
            )
        )
        edges.append(TopologyEdge(source=host_id, target=node_id, type="hosts"))
        if primary_ip:
            edges.append(TopologyEdge(source=gateway_id, target=node_id, type="network"))

    # Match each k8s Node to the VM that reports the same InternalIP among
    # its guest-agent-discovered addresses (heuristic -- keeps working if
    # the VM's IP changes, unlike a hardcoded vmid mapping).
    for kn in k8s_nodes:
        k8s_node_id = f"k8s-node-{kn['name']}"
        internal_ip = kn.get("internal_ip")
        matched_vm_id = None
        if internal_ip:
            for vm in vms:
                if internal_ip in (vm.get("ip_addresses") or []):
                    matched_vm_id = vm_id_by_vmid.get(vm["vmid"])
                    break
        nodes.append(
            TopologyNode(
                id=k8s_node_id,
                type="k8s_node",
                label=kn["name"],
                ip=internal_ip,
                status="ok",
            )
        )
        edges.append(
            TopologyEdge(
                source=matched_vm_id or host_id,
                target=k8s_node_id,
                type="runs_on",
            )
        )

    pod_node_id_by_key: dict[tuple[str, str], str] = {}
    for pod in pods:
        key = (pod["namespace"], pod["name"])
        pod_node_id = f"pod-{pod['namespace']}-{pod['name']}"
        pod_node_id_by_key[key] = pod_node_id
        status = "ok" if pod.get("status") == "Running" else "warn"
        nodes.append(
            TopologyNode(
                id=pod_node_id,
                type="k8s_pod",
                label=pod["name"],
                ip=pod.get("pod_ip"),
                status=status,
                meta={"namespace": pod["namespace"]},
            )
        )
        node_name = pod.get("node")
        parent_k8s_node_id = f"k8s-node-{node_name}" if node_name else None
        if parent_k8s_node_id and any(n.id == parent_k8s_node_id for n in nodes):
            edges.append(
                TopologyEdge(source=parent_k8s_node_id, target=pod_node_id, type="runs_on")
            )

    for svc in services:
        selector = svc.get("selector") or {}
        if not selector:
            continue
        svc_node_id = f"svc-{svc['namespace']}-{svc['name']}"
        matched_pod_ids = []
        for pod in pods:
            if pod["namespace"] != svc["namespace"]:
                continue
            labels = pod.get("labels") or {}
            if all(labels.get(k) == v for k, v in selector.items()):
                matched_pod_ids.append(pod_node_id_by_key[(pod["namespace"], pod["name"])])
        if not matched_pod_ids:
            continue
        nodes.append(
            TopologyNode(
                id=svc_node_id,
                type="k8s_service",
                label=svc["name"],
                ip=svc.get("cluster_ip"),
                status="ok",
                meta={"namespace": svc["namespace"]},
            )
        )
        for pod_node_id in matched_pod_ids:
            edges.append(TopologyEdge(source=svc_node_id, target=pod_node_id, type="exposes"))

    return Topology(
        nodes=nodes,
        edges=edges,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
