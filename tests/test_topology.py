from types import SimpleNamespace

from app.clients.kubernetes import _derive_display_name
from app.clients.proxmox import pick_lan_ip
from app.services.topology import build_topology


def test_pick_lan_ip_filters_k3s_interfaces():
    interfaces = [
        {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4"}]},
        {"name": "cni0", "ip-addresses": [{"ip-address": "10.42.0.1", "ip-address-type": "ipv4"}]},
        {"name": "flannel.1", "ip-addresses": [{"ip-address": "10.42.0.0", "ip-address-type": "ipv4"}]},
        {"name": "veth1234", "ip-addresses": [{"ip-address": "10.42.0.2", "ip-address-type": "ipv4"}]},
        {"name": "eth0", "ip-addresses": [{"ip-address": "192.168.1.50", "ip-address-type": "ipv4"}]},
    ]
    result = pick_lan_ip(interfaces, reference_host="192.168.1.10")
    assert result == ["192.168.1.50"]


def test_pick_lan_ip_prefers_reference_subnet():
    interfaces = [
        {"name": "eth0", "ip-addresses": [{"ip-address": "10.0.0.5", "ip-address-type": "ipv4"}]},
        {"name": "eth1", "ip-addresses": [{"ip-address": "192.168.1.77", "ip-address-type": "ipv4"}]},
    ]
    result = pick_lan_ip(interfaces, reference_host="192.168.1.10")
    assert result[0] == "192.168.1.77"


def test_pick_lan_ip_no_interfaces_returns_empty():
    assert pick_lan_ip([]) == []


def test_build_topology_basic_graph():
    vms = [
        {"vmid": 100, "name": "debian-k3s", "status": "running", "ip_addresses": ["192.168.1.50"]},
        {"vmid": 101, "name": "win11", "status": "running", "ip_addresses": ["192.168.1.60"]},
    ]
    k8s_nodes = [{"name": "debian-k3s", "internal_ip": "192.168.1.50"}]
    pods = [
        {
            "namespace": "default",
            "name": "myapp-abc123",
            "labels": {"app": "myapp"},
            "node": "debian-k3s",
            "pod_ip": "10.42.0.5",
            "status": "Running",
        }
    ]
    services = [
        {
            "namespace": "default",
            "name": "myapp",
            "cluster_ip": "10.43.0.5",
            "selector": {"app": "myapp"},
        }
    ]

    topo = build_topology(
        gateway_ip="192.168.1.1",
        gateway_label="Home Router",
        proxmox_host_label="pve",
        proxmox_host_ip="192.168.1.10",
        vms=vms,
        k8s_nodes=k8s_nodes,
        pods=pods,
        services=services,
    )

    node_ids = {n.id for n in topo.nodes}
    assert {"gateway", "proxmox-host", "vm-100", "vm-101", "k8s-node-debian-k3s"} <= node_ids
    assert "pod-default-myapp-abc123" in node_ids
    assert "svc-default-myapp" in node_ids

    # k8s node should be linked to the matching VM (by InternalIP), not
    # generically to the proxmox host.
    runs_on_edges = [e for e in topo.edges if e.type == "runs_on" and e.target == "k8s-node-debian-k3s"]
    assert runs_on_edges[0].source == "vm-100"

    # service -> pod edge exists because the selector matches pod labels
    expose_edges = [e for e in topo.edges if e.type == "exposes"]
    assert any(e.source == "svc-default-myapp" and e.target == "pod-default-myapp-abc123" for e in expose_edges)


def test_derive_display_name_strips_replicaset_hash():
    owner = [SimpleNamespace(kind="ReplicaSet", name="e2e-extended-74d6c86cbb")]
    assert _derive_display_name("e2e-extended-74d6c86cbb-f6q68", owner) == "e2e-extended"


def test_derive_display_name_daemonset_uses_owner_name_directly():
    owner = [SimpleNamespace(kind="DaemonSet", name="fluent-bit")]
    assert _derive_display_name("fluent-bit-9xzql", owner) == "fluent-bit"


def test_derive_display_name_no_owner_keeps_pod_name():
    assert _derive_display_name("standalone-pod", None) == "standalone-pod"
    assert _derive_display_name("standalone-pod", []) == "standalone-pod"


def test_derive_display_name_statefulset_keeps_ordinal_name():
    owner = [SimpleNamespace(kind="StatefulSet", name="myapp")]
    assert _derive_display_name("myapp-0", owner) == "myapp-0"


def test_build_topology_pod_label_uses_display_name():
    pods = [
        {
            "namespace": "default",
            "name": "myapp-74d6c86cbb-f6q68",
            "display_name": "myapp",
            "labels": {"app": "myapp"},
            "node": "debian-k3s",
            "pod_ip": "10.42.0.5",
            "status": "Running",
        }
    ]
    topo = build_topology(
        gateway_ip="192.168.1.1",
        gateway_label="Home Router",
        proxmox_host_label="pve",
        proxmox_host_ip=None,
        vms=[],
        k8s_nodes=[],
        pods=pods,
        services=[],
    )
    pod_node = next(n for n in topo.nodes if n.type == "k8s_pod")
    assert pod_node.label == "myapp"
    assert pod_node.meta["full_name"] == "myapp-74d6c86cbb-f6q68"


def test_build_topology_service_without_matching_pods_is_skipped():
    topo = build_topology(
        gateway_ip="192.168.1.1",
        gateway_label="Home Router",
        proxmox_host_label="pve",
        proxmox_host_ip=None,
        vms=[],
        k8s_nodes=[],
        pods=[],
        services=[{"namespace": "default", "name": "orphan", "cluster_ip": "10.43.0.9", "selector": {"app": "none"}}],
    )
    assert not any(n.type == "k8s_service" for n in topo.nodes)
