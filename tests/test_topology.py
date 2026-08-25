from types import SimpleNamespace

from app.clients.kubernetes import _derive_display_name
from app.clients.proxmox import (
    parse_linux_meminfo_used_bytes,
    parse_windows_meminfo_used_bytes,
    pick_lan_ip,
    pick_primary_filesystem,
)
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


# Real fsinfo payload from a Debian VM's guest agent: EFI boot partition
# plus the real root filesystem.
DEBIAN_FSINFO = [
    {"mountpoint": "/boot/efi", "name": "sda15", "total-bytes": 129718272, "used-bytes": 9322496, "type": "vfat"},
    {"mountpoint": "/", "name": "sda1", "total-bytes": 40298418176, "used-bytes": 25634947072, "type": "ext4"},
]

# Real fsinfo payload from a Windows 11 VM's guest agent: two mounted
# virtio-win/CD-ROM drives (CDFS), a UDF-mounted ISO, two "System
# Reserved" partitions with no size fields at all, and the real C: drive.
WINDOWS_FSINFO = [
    {"mountpoint": "F:\\", "name": "vol-f", "total-bytes": 789645312, "used-bytes": 789645312, "type": "CDFS"},
    {"mountpoint": "E:\\", "name": "vol-e", "total-bytes": 382976, "used-bytes": 382976, "type": "CDFS"},
    {"mountpoint": "D:\\", "name": "vol-d", "total-bytes": 8471603200, "used-bytes": 8471603200, "type": "UDF"},
    {"mountpoint": "System Reserved", "name": "vol-sr1", "type": "FAT32"},
    {"mountpoint": "System Reserved", "name": "vol-sr2", "type": "NTFS"},
    {"mountpoint": "C:\\", "name": "vol-c", "total-bytes": 106352865280, "used-bytes": 32028889088, "type": "NTFS"},
]


def test_pick_primary_filesystem_debian_picks_root_not_efi():
    used, total = pick_primary_filesystem(DEBIAN_FSINFO)
    assert (used, total) == (25634947072, 40298418176)


def test_pick_primary_filesystem_windows_picks_c_drive_not_cdrom_or_iso():
    used, total = pick_primary_filesystem(WINDOWS_FSINFO)
    assert (used, total) == (32028889088, 106352865280)


def test_pick_primary_filesystem_empty_returns_none():
    assert pick_primary_filesystem([]) == (None, None)


def test_pick_primary_filesystem_only_optical_media_returns_none():
    assert pick_primary_filesystem(
        [{"mountpoint": "D:\\", "total-bytes": 100, "used-bytes": 100, "type": "UDF"}]
    ) == (None, None)


# Real `cat /proc/meminfo` output from the Debian k3s VM, captured via
# guest-exec while investigating why Proxmox's own reported "used" (42GB)
# didn't match what `free` inside the guest said (24GB): the gap is
# reclaimable disk cache/buffers that MemAvailable already accounts for
# and MemFree doesn't.
REAL_LINUX_MEMINFO = """MemTotal:       49340092 kB
MemFree:         4990300 kB
MemAvailable:   23676524 kB
Buffers:          268928 kB
Cached:         18243816 kB
SwapCached:            0 kB
Active:         25799500 kB
Inactive:       17450872 kB
"""

REAL_WINDOWS_WMI_JSON = """{
    "TotalVisibleMemorySize":  16708008,
    "FreePhysicalMemory":  14657992
}
"""


def test_parse_linux_meminfo_uses_available_not_free():
    used = parse_linux_meminfo_used_bytes(REAL_LINUX_MEMINFO)
    assert used == (49340092 - 23676524) * 1024
    # Sanity: must NOT be the MemTotal-MemFree figure Proxmox's balloon
    # driver reports, which is what made this look ~2x too high.
    assert used != (49340092 - 4990300) * 1024


def test_parse_linux_meminfo_missing_fields_returns_none():
    assert parse_linux_meminfo_used_bytes("SomeOtherField: 123 kB\n") is None
    assert parse_linux_meminfo_used_bytes("") is None


def test_parse_windows_meminfo():
    used = parse_windows_meminfo_used_bytes(REAL_WINDOWS_WMI_JSON)
    assert used == (16708008 - 14657992) * 1024


def test_parse_windows_meminfo_malformed_returns_none():
    assert parse_windows_meminfo_used_bytes("not json") is None
    assert parse_windows_meminfo_used_bytes('{"TotalVisibleMemorySize": 100}') is None


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


def test_build_topology_pods_hang_off_a_namespace_node_not_the_k8s_node_directly():
    k8s_nodes = [{"name": "debian-k3s", "internal_ip": None}]
    pods = [
        {"namespace": "default", "name": "app-a", "labels": {}, "node": "debian-k3s", "pod_ip": "10.42.0.1", "status": "Running"},
        {"namespace": "default", "name": "app-b", "labels": {}, "node": "debian-k3s", "pod_ip": "10.42.0.2", "status": "Running"},
        {"namespace": "kube-tools", "name": "tool-a", "labels": {}, "node": "debian-k3s", "pod_ip": "10.42.0.3", "status": "Running"},
    ]

    topo = build_topology(
        gateway_ip="192.168.1.1",
        gateway_label="Home Router",
        proxmox_host_label="pve",
        proxmox_host_ip=None,
        vms=[],
        k8s_nodes=k8s_nodes,
        pods=pods,
        services=[],
    )

    namespace_nodes = {n.id: n for n in topo.nodes if n.type == "namespace"}
    assert set(namespace_nodes) == {"ns-default", "ns-kube-tools"}

    # No pod is a direct target of an edge sourced from the k8s node --
    # everything routes through its namespace node instead.
    assert not any(
        e.source == "k8s-node-debian-k3s" and e.target.startswith("pod-") for e in topo.edges
    )
    # Exactly one k8s-node -> namespace edge per namespace (not one per pod).
    node_to_ns_edges = [e for e in topo.edges if e.source == "k8s-node-debian-k3s" and e.target in namespace_nodes]
    assert len(node_to_ns_edges) == 2
    # Both of default's pods hang off the same namespace node.
    default_pod_edges = {e.target for e in topo.edges if e.source == "ns-default"}
    assert default_pod_edges == {"pod-default-app-a", "pod-default-app-b"}


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


def test_build_topology_includes_new_client_devices():
    clients = [
        {"ip": "192.168.1.77", "name": "johns-iphone", "mac": "AA:BB:CC:DD:EE:FF", "active": True, "interface_type": "802.11"},
    ]
    topo = build_topology(
        gateway_ip="192.168.1.1",
        gateway_label="Home Router",
        proxmox_host_label="pve",
        proxmox_host_ip="192.168.1.10",
        vms=[],
        k8s_nodes=[],
        pods=[],
        services=[],
        clients=clients,
    )
    client_node = next(n for n in topo.nodes if n.type == "client")
    assert client_node.id == "client-192-168-1-77"
    assert client_node.label == "johns-iphone"
    assert client_node.status == "ok"
    assert any(e.source == "gateway" and e.target == client_node.id for e in topo.edges)


def test_build_topology_deduplicates_clients_already_known():
    vms = [{"vmid": 100, "name": "debian-k3s", "status": "running", "ip_addresses": ["192.168.1.50"]}]
    clients = [
        {"ip": "192.168.1.10", "name": "proxmox-again", "mac": "AA:AA:AA:AA:AA:AA", "active": True, "interface_type": "Ethernet"},
        {"ip": "192.168.1.50", "name": "vm-again", "mac": "BB:BB:BB:BB:BB:BB", "active": True, "interface_type": "Ethernet"},
        {"ip": "192.168.1.99", "name": "actually-new", "mac": "CC:CC:CC:CC:CC:CC", "active": False, "interface_type": "802.11"},
    ]
    topo = build_topology(
        gateway_ip="192.168.1.1",
        gateway_label="Home Router",
        proxmox_host_label="pve",
        proxmox_host_ip="192.168.1.10",
        vms=vms,
        k8s_nodes=[],
        pods=[],
        services=[],
        clients=clients,
    )
    client_nodes = [n for n in topo.nodes if n.type == "client"]
    assert len(client_nodes) == 1
    assert client_nodes[0].label == "actually-new"
    assert client_nodes[0].status == "unknown"  # inactive device


def test_build_topology_no_clients_arg_is_fine():
    topo = build_topology(
        gateway_ip="192.168.1.1",
        gateway_label="Home Router",
        proxmox_host_label="pve",
        proxmox_host_ip=None,
        vms=[],
        k8s_nodes=[],
        pods=[],
        services=[],
    )
    assert not any(n.type == "client" for n in topo.nodes)


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
